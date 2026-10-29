#!/usr/bin/env python3
"""
Детализация SLA по этапам воронки заявок.
"""
import html
import json
import statistics
from datetime import datetime

import psycopg2
from flask import Flask, request, Response, jsonify
from psycopg2.extras import Json

from config import DB_CONFIG, STATUS_LABELS
from last_comment import extract_bank_comment_list, fetch_bank_request_bundle
from lib.region_heatmap import fetch_heatmap_payload, render_heatmap_section

app = Flask(__name__)

MS_DAY = 86400000
MAX_STAGE_DAYS = 365
DETAIL_LIMIT = 100

EXCLUDED_STATUSES = {
    'CANCELED_BY_CLIENT',
    'CANCELED_BY_BANK',
    'REFUND',
}

# Статус кассы/посылки для этапа «Отправка → доставка».
# DELIVERED в CRM соответствует финалу Почты России.
DELIVERY_STATUS_LABELS = {
    'NEW': 'Новая',
    'QUEUE': 'В очереди',
    'DISTRIBUTED': 'Распределена',
    'READY': 'Готова к выдаче',
    'WAIT_ORDER': 'Ожидание заказа',
    'CDEK_FF_PENDING': 'Ожидание CDEK FF',
    'WAIT_COURIER_ASSIGN': 'Ожидание курьера',
    'SHIPMENT': 'Отгрузка',
    'DELIVERY': 'В доставке',
    'DELIVERED': 'Успешно доставлен и вручен адресату (конечный статус)',
    'ON_THE_WAY_CDEK_FF': 'В пути CDEK FF',
    'REFUND': 'Возврат',
}

# Идеальные сроки. Всё, что дольше — невыполнение (подсветка bad).
# good хранится в сутках.
SLA_STAGES = [
    {
        'key': 'lead_to_invoice',
        'title': 'Заявка → выставление счёта',
        'from_field': 'created',
        'to_field': 'incomeSetDate',
        'group': 'stages',
        'good': 1.0,
        'unit': 'days',
        'show_city': False,
    },
    {
        'key': 'invoice_to_payment',
        'title': 'Счёт → оплата',
        'from_field': 'incomeSetDate',
        'to_field': 'incomePaidDate',
        'group': 'stages',
        'good': 1.0,
        'unit': 'days',
        'show_city': False,
    },
    {
        'key': 'payment_to_sent',
        'title': 'Оплата → отправка в доставку',
        'from_field': 'incomePaidDate',
        'to_field': 'sentToDeliveryDate',
        'group': 'stages',
        'good': 1.0,
        'unit': 'days',
        'show_city': True,
    },
    {
        'key': 'sent_to_delivered',
        'title': 'Отправка → доставка',
        'from_field': 'sentToDeliveryDate',
        'to_field': 'deliveredDate',
        'group': 'stages',
        'good': 6.0,
        'unit': 'days',
        'show_city': True,
        'status_source': 'delivery',
    },
    {
        'key': 'delivered_to_install',
        'title': 'Обучение после доставки',
        'from_field': 'deliveredDate',
        'to_field': 'kkmRegistrationDate',
        'group': 'stages',
        'good': 1.0,
        'unit': 'days',
        'show_city': False,
    },
    {
        'key': 'cjm_created_to_install',
        'title': 'CJM: получение заявки → установка',
        'from_field': 'created',
        'to_field': 'kkmRegistrationDate',
        'group': 'cjm',
        'good': 10.0,
        'unit': 'days',
        'show_city': False,
    },
    {
        'key': 'cjm_invoice_to_install',
        'title': 'CJM: счёт → установка',
        'from_field': 'incomeSetDate',
        'to_field': 'kkmRegistrationDate',
        'group': 'cjm',
        'good': 7.0,
        'unit': 'days',
        'show_city': False,
    },
]


def parse_sla_period():
    """Период из query start/end (по умолчанию — текущий месяц)."""
    start_param = request.args.get('start')
    end_param = request.args.get('end')

    if start_param and end_param:
        try:
            start_date = datetime.strptime(start_param, '%Y-%m-%d')
            end_date = datetime.strptime(end_param, '%Y-%m-%d')
        except ValueError:
            now = datetime.now()
            start_date = now.replace(day=1)
            end_date = now
    else:
        now = datetime.now()
        start_date = now.replace(day=1)
        end_date = now

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    search_start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    search_end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    return search_start, search_end


def format_duration(days, unit='days'):
    """Формат срока: часы для этапа счёта, иначе дни."""
    if days is None:
        return "—"
    if unit == 'hours':
        hours = float(days) * 24
        if abs(hours - round(hours)) < 0.05:
            return f"{int(round(hours))} ч"
        return f"{hours:.1f}".replace(".", ",") + " ч"
    if abs(float(days) - round(float(days))) < 0.05:
        return f"{int(round(float(days)))} дн"
    return f"{float(days):.1f}".replace(".", ",") + " дн"


def format_days(value):
    return format_duration(value, 'days')


def format_number(value):
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        if value == int(value):
            return f"{int(value):,}".replace(",", " ")
        return f"{value:.2f}".replace(".", ",")
    return str(value)


def tone_lower_better(value, good, warning=None):
    """good — идеал; всё, что дольше, считается невыполнением."""
    if value is None:
        return "neutral"
    if value <= good + 1e-9:
        return "good"
    if warning is not None and warning > good and value <= warning:
        return "warning"
    return "bad"


def ms_to_days(ms):
    return round(ms / MS_DAY, 4)


def parse_ms(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_ts(ms):
    if not ms:
        return "—"
    try:
        return datetime.fromtimestamp(ms / 1000).strftime('%d.%m.%Y %H:%M')
    except (OSError, OverflowError, ValueError):
        return "—"


def extract_delivery_address(data):
    """Адрес доставки ККТ (kkmDeliveryAddress через enrichment)."""
    if not isinstance(data, dict):
        return "—"

    candidates = (
        'delivery_address',
        'kkmDeliveryAddress',
        'deliveryAddress',
    )

    def from_mapping(obj):
        if not isinstance(obj, dict):
            return None
        for key in candidates:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    enrichment = data.get('enrichment') if isinstance(data.get('enrichment'), dict) else {}
    for source in (enrichment, data):
        found = from_mapping(source)
        if found:
            return found
    return "—"


def _city_from_delivery_address(address):
    """Населённый пункт из строки адреса доставки ККТ."""
    if not isinstance(address, str) or not address.strip() or address == "—":
        return None
    parts = [p.strip() for p in address.split(',') if p.strip()]
    prefixes = (
        'г ', 'г.', 'город ',
        'п ', 'п.', 'посёлок ', 'поселок ', 'рп ',
        'с ', 'с.', 'село ',
        'деревня ', 'ст-ца ', 'станица ',
        'аул ', 'хутор ',
    )
    for part in parts:
        low = part.lower()
        if low.startswith('д.') or low.startswith('д '):
            rest = part.split('.', 1)[-1].strip() if '.' in part else part[1:].strip()
            if rest[:1].isdigit():
                continue
        if any(low.startswith(prefix) for prefix in prefixes):
            return part
    return None


def extract_delivery_city(data):
    """Город получателя из заявки / enrichment (если уже обогащено)."""
    if not isinstance(data, dict):
        return "—"

    candidates = (
        'delivery_city',
        'deliveryCity',
        'recipientCity',
        'clientCity',
        'city',
        'cityName',
    )

    def from_mapping(obj):
        if not isinstance(obj, dict):
            return None
        for key in candidates:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                for nested_key in ('name', 'title', 'value', 'city', 'cityName'):
                    nested = value.get(nested_key)
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
        for nest_key in ('delivery', 'deliveryAddress', 'address', 'recipient', 'client'):
            nest = obj.get(nest_key)
            if isinstance(nest, dict):
                found = from_mapping(nest)
                if found:
                    return found
                for nested_key in ('city', 'cityName', 'name', 'settlement', 'town'):
                    nested = nest.get(nested_key)
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
        return None

    enrichment = data.get('enrichment') if isinstance(data.get('enrichment'), dict) else {}
    for source in (enrichment, data):
        found = from_mapping(source)
        if found:
            return found
    city_from_addr = _city_from_delivery_address(extract_delivery_address(data))
    if city_from_addr:
        return city_from_addr
    return "—"


def extract_delivery_status(data):
    """Статус доставки ККТ.

    Если есть deliveredDate — это финал Почты России.
    Иначе берём статус кассы из enrichment или факт отправки.
    """
    if not isinstance(data, dict):
        return "—"
    if parse_ms(data.get('deliveredDate')) is not None:
        return DELIVERY_STATUS_LABELS['DELIVERED']
    enrichment = data.get('enrichment') if isinstance(data.get('enrichment'), dict) else {}
    raw = enrichment.get('delivery_status') or data.get('delivery_status')
    if isinstance(raw, str) and raw.strip():
        code = raw.strip()
        return DELIVERY_STATUS_LABELS.get(code, code)
    if parse_ms(data.get('sentToDeliveryDate')) is not None:
        return DELIVERY_STATUS_LABELS['DELIVERY']
    return "—"


def get_crm_token():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT current_token FROM auth_tokens WHERE service = 'crm'")
    result = cur.fetchone()
    cur.close()
    conn.close()
    if not result or not result[0]:
        raise ValueError("Токен CRM не найден")
    return result[0]


def cache_bank_comments(request_id, bundle):
    """Пишем историю статусов в enrichment, не затирая остальные поля."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT data FROM requests WHERE id = %s", (request_id,))
    row = cur.fetchone()
    if not row or not isinstance(row[0], dict):
        cur.close()
        conn.close()
        return
    data = row[0]
    enrichment = data.get("enrichment") if isinstance(data.get("enrichment"), dict) else {}
    bank_request = (
        enrichment.get("bank_request")
        if isinstance(enrichment.get("bank_request"), dict)
        else {}
    )
    bank_request.update(bundle or {})
    enrichment["bank_request"] = bank_request
    data["enrichment"] = enrichment
    cur.execute(
        "UPDATE requests SET data = %s, updated_at = %s WHERE id = %s",
        (Json(data), datetime.now(), request_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def render_crm_comments_table(items):
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
        "<thead><tr>"
        "<th>Отправитель</th><th>Комментарий</th><th>Дата создания</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def render_crm_history_block(item):
    comments = item.get("crm_comments") or []
    loaded = "1" if comments else "0"
    count = f" ({len(comments)})" if comments else ""
    body = (
        render_crm_comments_table(comments)
        if comments
        else '<div class="crm-loading">Загрузка истории статусов…</div>'
    )
    return (
        f'<details class="crm-history" data-request-id="{item["id"]}" data-loaded="{loaded}">'
        f"<summary>История статусов{count}</summary>"
        f'<div class="crm-history-body">{body}</div>'
        "</details>"
    )


def load_comments(request_ids):
    """Комментарии по заявкам: {(request_id, stage_key): comment}."""
    if not request_ids:
        return {}
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT request_id, stage_key, comment
            FROM sla_stage_comments
            WHERE request_id = ANY(%s)
            """,
            (list(request_ids),),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {(int(r[0]), r[1]): (r[2] or '') for r in rows}
    except Exception as e:
        print(f"❌ Ошибка чтения sla_stage_comments: {e}")
        return {}


def upsert_comment(request_id, stage_key, comment):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sla_stage_comments (request_id, stage_key, comment, updated_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (request_id, stage_key) DO UPDATE
        SET comment = EXCLUDED.comment,
            updated_at = CURRENT_TIMESTAMP
        """,
        (request_id, stage_key, comment),
    )
    conn.commit()
    cur.close()
    conn.close()


def fetch_period_requests(start_dt, end_dt):
    """Заявки периода по created (исключая отмены/refund)."""
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


def build_stage_stats(rows, stage):
    """Сводка и детализация по одному этапу."""
    from_field = stage['from_field']
    to_field = stage['to_field']
    show_city = bool(stage.get('show_city'))
    samples = []
    city_days = {}

    for req_id, data in rows:
        start_ms = parse_ms(data.get(from_field))
        end_ms = parse_ms(data.get(to_field))
        if start_ms is None or end_ms is None:
            continue
        delta = end_ms - start_ms
        if delta < 0 or delta > MAX_STAGE_DAYS * MS_DAY:
            continue
        days = ms_to_days(delta)
        address = extract_delivery_address(data)
        city = extract_delivery_city(data) if show_city else "—"
        if show_city and city == "—" and address != "—":
            city = _city_from_delivery_address(address) or "—"
        row_tone = tone_lower_better(days, stage['good'])
        if stage.get('status_source') == 'delivery':
            status_label = extract_delivery_status(data)
        else:
            status_label = STATUS_LABELS.get(data.get('status'), data.get('status') or 'UNKNOWN')
        samples.append({
            'id': req_id,
            'organization': data.get('organizationName') or '—',
            'contact': data.get('contactName') or '—',
            'status': data.get('status') or 'UNKNOWN',
            'status_label': status_label,
            'from_ms': start_ms,
            'to_ms': end_ms,
            'from_label': format_ts(start_ms),
            'to_label': format_ts(end_ms),
            'days': days,
            'city': city,
            'address': address,
            'tone': row_tone,
            'crm_comments': extract_bank_comment_list(data),
        })
        if show_city and city != "—":
            city_days.setdefault(city, []).append(days)

    if not samples:
        return {
            'key': stage['key'],
            'title': stage['title'],
            'group': stage['group'],
            'good': stage['good'],
            'unit': stage.get('unit') or 'days',
            'show_city': show_city,
            'status_source': stage.get('status_source') or 'crm',
            'avg': None,
            'med': None,
            'max': None,
            'n': 0,
            'overdue_n': 0,
            'tone': 'neutral',
            'details': [],
            'city_stats': [],
        }

    days_list = [s['days'] for s in samples]
    samples.sort(key=lambda s: s['days'], reverse=True)
    details = samples[:DETAIL_LIMIT]
    prec = 4 if stage.get('unit') == 'hours' else 2
    med = round(statistics.median(days_list), prec)
    avg = round(statistics.mean(days_list), prec)
    mx = round(max(days_list), prec)
    overdue_n = sum(1 for s in samples if s.get('tone') == 'bad')

    city_stats = []
    for city, values in city_days.items():
        city_stats.append({
            'city': city,
            'n': len(values),
            'med': round(statistics.median(values), prec),
            'avg': round(statistics.mean(values), prec),
            'max': round(max(values), prec),
            'tone': tone_lower_better(statistics.median(values), stage['good']),
        })
    city_stats.sort(key=lambda item: (-item['med'], -item['n'], item['city']))

    return {
        'key': stage['key'],
        'title': stage['title'],
        'group': stage['group'],
        'good': stage['good'],
        'unit': stage.get('unit') or 'days',
        'show_city': show_city,
        'status_source': stage.get('status_source') or 'crm',
        'avg': avg,
        'med': med,
        'max': mx,
        'n': len(samples),
        'overdue_n': overdue_n,
        'tone': tone_lower_better(med, stage['good']),
        'details': details,
        'city_stats': city_stats[:30],
    }


def compute_sla_detail(start_dt, end_dt):
    rows = fetch_period_requests(start_dt, end_dt)
    stages = [build_stage_stats(rows, stage) for stage in SLA_STAGES]

    request_ids = set()
    for stage in stages:
        for item in stage['details']:
            request_ids.add(item['id'])

    comments = load_comments(request_ids)
    for stage in stages:
        for item in stage['details']:
            item['comment'] = comments.get((item['id'], stage['key']), '')

    return stages


def render_detail_rows(stage):
    show_city = bool(stage.get('show_city'))
    unit = stage.get('unit') or 'days'
    col_span = 9 if show_city else 8
    if not stage['details']:
        return f'<tr><td colspan="{col_span}" class="empty">Нет данных за период</td></tr>'

    rows_html = []
    for item in stage['details']:
        comment_val = html.escape(item.get('comment') or '')
        city_cell = (
            f'<td>{html.escape(str(item.get("city") or "—"))}</td>'
            if show_city else ''
        )
        address = html.escape(str(item.get('address') or '—'))
        overdue_flag = (
            '<div class="overdue-flag">просрочено</div>'
            if item.get('tone') == 'bad' else ''
        )
        rows_html.append(
            f'''<tr class="row-{item.get('tone', 'neutral')}">
                <td>{item['id']}</td>
                <td>
                    <div class="org">{html.escape(str(item['organization']))}</div>
                    <div class="contact">{html.escape(str(item['contact']))}</div>
                </td>
                {city_cell}
                <td class="addr">{address}</td>
                <td class="status-cell">{html.escape(item['status_label'])}</td>
                <td>{html.escape(item['from_label'])}</td>
                <td>{html.escape(item['to_label'])}</td>
                <td class="days {item.get('tone', 'neutral')}">{format_duration(item['days'], unit)}{overdue_flag}</td>
                <td>
                    <div class="comment-box"
                         data-request-id="{item['id']}"
                         data-stage-key="{html.escape(stage['key'])}">
                        <textarea rows="2" placeholder="Внутренний комментарий…">{comment_val}</textarea>
                        <button type="button" onclick="saveComment(this)">Сохранить</button>
                        <span class="save-status"></span>
                    </div>
                </td>
            </tr>
            <tr class="crm-history-row">
                <td colspan="{col_span}">{render_crm_history_block(item)}</td>
            </tr>'''
        )
    return ''.join(rows_html)


def render_stage_card(stage):
    details_html = render_detail_rows(stage)
    city_header = '<th>Город</th>' if stage.get('show_city') else ''
    status_header = (
        'Статус доставки'
        if stage.get('status_source') == 'delivery'
        else 'Статус'
    )
    unit = stage.get('unit') or 'days'
    overdue_n = int(stage.get('overdue_n') or 0)
    n_total = int(stage.get('n') or 0)
    overdue_line = ''
    if n_total:
        overdue_cls = 'overdue-stat' if overdue_n else 'overdue-stat ok'
        overdue_line = (
            f'<div class="{overdue_cls}">Невыполнение: '
            f'{format_number(overdue_n)} из {format_number(n_total)}</div>'
        )
    return f'''
    <section class="stage-card {stage['tone']}">
        <div class="stage-top">
            <div>
                <h3>{html.escape(stage['title'])}</h3>
                <div class="etalon">Идеал ≤ {format_duration(stage['good'], unit)}. Дольше — невыполнение.</div>
                {overdue_line}
                <div class="caption">Медиана</div>
                <div class="med {stage['tone']}">{format_duration(stage['med'], unit)}</div>
            </div>
            <div class="stats">
                <div><span>Среднее</span><strong class="{stage['tone']}">{format_duration(stage['avg'], unit)}</strong></div>
                <div><span>Максимум</span><strong class="{stage['tone']}">{format_duration(stage['max'], unit)}</strong></div>
                <div><span>Выборка</span><strong>n={format_number(stage['n'])}</strong></div>
            </div>
        </div>
        <details class="stage-details">
            <summary>Детализация (топ-{DETAIL_LIMIT} самых долгих)</summary>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Клиент</th>
                            {city_header}
                            <th>Адрес доставки ККТ</th>
                            <th>{status_header}</th>
                            <th>Начало</th>
                            <th>Конец</th>
                            <th>Срок</th>
                            <th>Комментарий</th>
                        </tr>
                    </thead>
                    <tbody>
                        {details_html}
                    </tbody>
                </table>
            </div>
        </details>
    </section>
    '''


def generate_html(stages, start_dt, end_dt):
    stage_cards = ''.join(
        render_stage_card(s) for s in stages if s['group'] == 'stages'
    )
    cjm_cards = ''.join(
        render_stage_card(s) for s in stages if s['group'] == 'cjm'
    )
    period_label = (
        f"{start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}"
    )
    start_val = start_dt.strftime('%Y-%m-%d')
    end_val = end_dt.strftime('%Y-%m-%d')

    return f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Детализация SLA</title>
    <style>
        :root {{
            --bg: #eef2f7;
            --card: #ffffff;
            --text: #1f2a37;
            --muted: #6b7c93;
            --line: #e6ebf2;
            --good: #0f9d58;
            --warning: #d97706;
            --bad: #dc2626;
            --accent: #2563eb;
            --header-from: #1e3a5f;
            --header-to: #2563eb;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(180deg, #f7f9fc 0%, var(--bg) 100%);
            color: var(--text);
            min-height: 100vh;
        }}
        .header {{
            background: linear-gradient(135deg, var(--header-from), var(--header-to));
            color: #fff;
            padding: 24px 24px 28px;
        }}
        .header-inner {{ max-width: none; margin: 0 auto; }}
        .header-top {{
            display: flex; justify-content: space-between; gap: 16px;
            flex-wrap: wrap; align-items: flex-start; margin-bottom: 16px;
        }}
        h1 {{ font-size: 1.8rem; font-weight: 650; }}
        .sub {{ opacity: 0.85; margin-top: 6px; }}
        .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .btn {{
            display: inline-flex; align-items: center; padding: 9px 12px;
            border-radius: 10px; text-decoration: none; color: #fff;
            border: 1px solid rgba(255,255,255,0.25); background: rgba(255,255,255,0.12);
            font-weight: 600; font-size: 0.9rem;
        }}
        .period-bar {{
            display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
        }}
        .period-bar label {{ font-size: 0.85rem; opacity: 0.85; }}
        .period-bar input[type="date"] {{
            border: 0; border-radius: 8px; padding: 7px 10px; font-size: 0.9rem;
        }}
        .period-bar button, .quick button {{
            border: 1px solid rgba(255,255,255,0.3); background: rgba(255,255,255,0.14);
            color: #fff; border-radius: 8px; padding: 7px 12px; font-weight: 600;
            cursor: pointer; font-size: 0.85rem;
        }}
        .period-bar button.primary {{ background: #fff; color: var(--header-from); border-color: #fff; }}
        .quick {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }}
        .container {{ max-width: none; margin: 0 auto; padding: 20px 24px; }}
        .section-title {{
            font-size: 1.1rem; margin: 8px 0 12px; font-weight: 650;
        }}
        .stage-card {{
            background: var(--card); border: 1px solid rgba(226,232,240,0.9);
            border-radius: 16px; padding: 18px; margin-bottom: 14px;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.05);
            border-top: 3px solid var(--accent);
        }}
        .stage-card.good {{ border-top-color: var(--good); background: #f3fbf6; }}
        .stage-card.warning {{
            border-top-color: var(--warning);
            background: #fff8eb;
            box-shadow: 0 0 0 1px rgba(217, 119, 6, 0.25), 0 10px 30px rgba(15, 23, 42, 0.05);
        }}
        .stage-card.bad {{
            border-top-color: var(--bad);
            background: #fff1f1;
            box-shadow: 0 0 0 2px rgba(220, 38, 38, 0.28), 0 10px 30px rgba(15, 23, 42, 0.06);
        }}
        .stage-top {{
            display: flex; justify-content: space-between; gap: 20px; flex-wrap: wrap;
        }}
        .stage-top h3 {{ font-size: 1.05rem; margin-bottom: 6px; }}
        .etalon {{
            color: var(--muted);
            font-size: 0.82rem;
            margin-bottom: 6px;
        }}
        .overdue-stat {{
            color: var(--bad);
            font-size: 0.85rem;
            font-weight: 650;
            margin-bottom: 10px;
        }}
        .overdue-stat.ok {{ color: var(--good); }}
        .overdue-flag {{
            display: block;
            margin-top: 4px;
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.03em;
            color: var(--bad);
        }}
        .caption {{
            font-size: 0.75rem; color: var(--muted); text-transform: uppercase;
            letter-spacing: 0.04em; font-weight: 600;
        }}
        .med {{ font-size: 2rem; font-weight: 700; letter-spacing: -0.03em; }}
        .med.good {{ color: var(--good); }}
        .med.warning {{ color: var(--warning); }}
        .med.bad {{ color: var(--bad); }}
        .stats strong.good {{ color: var(--good); }}
        .stats strong.warning {{ color: var(--warning); }}
        .stats strong.bad {{ color: var(--bad); }}
        .days.good {{ color: var(--good); }}
        .days.warning {{ color: var(--warning); }}
        .days.bad {{ color: var(--bad); font-weight: 800; }}
        tr.row-warning td {{ background: #fff7ed; }}
        tr.row-bad td {{ background: #fecaca; }}
        tr.row-bad {{ box-shadow: inset 4px 0 0 var(--bad); }}
        .city-block {{
            margin-top: 14px;
            padding-top: 12px;
            border-top: 1px dashed var(--line);
        }}
        .city-block h4 {{
            font-size: 0.92rem;
            margin-bottom: 8px;
        }}
        .city-empty {{
            margin-top: 12px;
            color: var(--muted);
            font-size: 0.85rem;
        }}
        .stats {{
            display: grid; gap: 8px; min-width: 200px;
        }}
        .stats div {{
            display: flex; justify-content: space-between; gap: 12px;
            color: var(--muted); font-size: 0.9rem;
        }}
        .stats strong {{ color: var(--text); }}
        .stage-details {{ margin-top: 14px; }}
        .stage-details > summary {{
            cursor: pointer; font-weight: 650; color: var(--accent);
            list-style: none; padding: 8px 0;
        }}
        .stage-details > summary::-webkit-details-marker {{ display: none; }}
        .stage-details > summary::before {{ content: "▸ "; }}
        .stage-details[open] > summary::before {{ content: "▾ "; }}
        .table-wrap {{ overflow-x: auto; margin-top: 8px; }}
        table {{
            width: 100%; border-collapse: collapse; font-size: 0.88rem;
        }}
        th, td {{
            border-bottom: 1px solid var(--line); padding: 10px 8px;
            text-align: left; vertical-align: top;
        }}
        th {{ color: var(--muted); font-weight: 650; white-space: nowrap; }}
        .org {{ font-weight: 650; }}
        .contact {{ color: var(--muted); font-size: 0.82rem; margin-top: 2px; }}
        .addr {{
            max-width: 320px;
            line-height: 1.35;
            color: #374151;
            white-space: normal;
        }}
        .status-cell {{
            max-width: 280px;
            line-height: 1.35;
            white-space: normal;
        }}
        .days {{ font-weight: 700; white-space: nowrap; }}
        .empty {{ color: var(--muted); text-align: center; }}
        .comment-box {{ display: grid; gap: 6px; min-width: 220px; }}
        .comment-box textarea {{
            width: 100%; border: 1px solid var(--line); border-radius: 8px;
            padding: 8px; font: inherit; resize: vertical;
        }}
        .comment-box button {{
            justify-self: start; border: 0; background: var(--accent); color: #fff;
            border-radius: 8px; padding: 6px 10px; font-weight: 650; cursor: pointer;
        }}
        .save-status {{ font-size: 0.78rem; color: var(--muted); min-height: 1em; }}
        .save-status.ok {{ color: var(--good); }}
        .save-status.err {{ color: var(--bad); }}
        .crm-history-row td {{
            background: #fff;
            padding: 0 10px 12px;
            border-bottom: 1px solid var(--line);
        }}
        .crm-history summary {{
            cursor: pointer;
            color: var(--accent);
            font-weight: 650;
            font-size: 0.86rem;
            list-style: none;
            padding: 6px 0 4px;
        }}
        .crm-history summary::-webkit-details-marker {{ display: none; }}
        .crm-history summary::before {{ content: "▸ "; }}
        .crm-history[open] summary::before {{ content: "▾ "; }}
        .crm-comments {{
            width: 100%;
            max-width: 960px;
            border-collapse: collapse;
            font-size: 0.84rem;
            margin-top: 4px;
        }}
        .crm-comments th {{
            background: #f3f4f6;
            color: #4b5563;
            font-weight: 650;
            white-space: nowrap;
        }}
        .crm-comments th, .crm-comments td {{
            border-bottom: 1px solid #e5e7eb;
            padding: 8px 10px;
            text-align: left;
        }}
        .crm-comments .crm-date {{
            white-space: nowrap;
            color: #6b7280;
        }}
        .crm-empty, .crm-loading, .crm-error {{
            color: var(--muted);
            font-size: 0.85rem;
            padding: 8px 0;
        }}
        .crm-error {{ color: var(--bad); }}
        .note {{ color: var(--muted); font-size: 0.85rem; margin-top: 8px; }}
        @media (max-width: 900px) {{
            .med {{ font-size: 1.6rem; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <div class="header-top">
                <div>
                    <h1>Детализация SLA</h1>
                    <div class="sub">Период по дате создания заявки: {period_label}</div>
                </div>
                <div class="actions">
                    <a class="btn" href="/dashboard">Дашборд</a>
                    <a class="btn" href="/inventory">Инвентаризация</a>
                    <a class="btn" href="/">На главную</a>
                    <a class="btn" href="/report">Отчёт CRM</a>
                </div>
            </div>
            <form class="period-bar" method="GET" action="/sla" id="sla-period-form">
                <label for="sla-start">с</label>
                <input id="sla-start" type="date" name="start" value="{start_val}" required>
                <label for="sla-end">по</label>
                <input id="sla-end" type="date" name="end" value="{end_val}" required>
                <button class="primary" type="submit">Показать</button>
            </form>
            <div class="quick">
                <button type="button" onclick="setSlaPeriod(7)">7 дней</button>
                <button type="button" onclick="setSlaPeriod(30)">30 дней</button>
                <button type="button" onclick="setSlaPeriod('month')">Этот месяц</button>
                <button type="button" onclick="setSlaPeriod(90)">90 дней</button>
            </div>
        </div>
    </div>

    <div class="container">
        <!--HEATMAP-->
        <h2 class="section-title">Этапы воронки</h2>
        {stage_cards}

        <h2 class="section-title">Общий CJM</h2>
        {cjm_cards}

        <p class="note">
            Идеалы: счёт 1 дн., оплата 1 дн., отправка 1 дн., доставка 6 дн.,
            обучение 1 дн., заявка→установка 10 дн., счёт→установка 7 дн.
            Срок выше идеала подсвечен как невыполнение.
            Отменённые и refund исключены. В детализации — до {DETAIL_LIMIT} самых долгих кейсов.
            Адрес доставки ККТ берётся из поля CRM <code>kkmDeliveryAddress</code> после обогащения заявки.
            На этапе «Отправка → доставка» в колонке «Статус доставки» финал —
            «Успешно доставлен и вручен адресату (конечный статус)».
            Комментарии сохраняются в БД по заявке и этапу.
            История статусов CRM открывается в каждой строке (отправитель, комментарий, дата).
            Тепловая карта считает отправки с января года даты «по»; регион берётся из адреса доставки ККТ.
        </p>
    </div>

    <script>
        function formatDateISO(d) {{
            const y = d.getFullYear();
            const m = String(d.getMonth() + 1).padStart(2, '0');
            const day = String(d.getDate()).padStart(2, '0');
            return `${{y}}-${{m}}-${{day}}`;
        }}
        function setSlaPeriod(period) {{
            const end = new Date();
            let start = new Date();
            if (period === 'month') {{
                start = new Date(end.getFullYear(), end.getMonth(), 1);
            }} else {{
                start.setDate(end.getDate() - Number(period) + 1);
            }}
            document.getElementById('sla-start').value = formatDateISO(start);
            document.getElementById('sla-end').value = formatDateISO(end);
            document.getElementById('sla-period-form').submit();
        }}
        async function saveComment(btn) {{
            const box = btn.closest('.comment-box');
            const status = box.querySelector('.save-status');
            const textarea = box.querySelector('textarea');
            status.className = 'save-status';
            status.textContent = 'Сохранение…';
            try {{
                const resp = await fetch('/sla/comment', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        request_id: Number(box.dataset.requestId),
                        stage_key: box.dataset.stageKey,
                        comment: textarea.value
                    }})
                }});
                const data = await resp.json();
                if (!resp.ok) throw new Error(data.error || 'Ошибка сохранения');
                status.className = 'save-status ok';
                status.textContent = 'Сохранено';
            }} catch (e) {{
                status.className = 'save-status err';
                status.textContent = e.message || 'Ошибка';
            }}
        }}
        const crmHistoryCache = {{}};
        function renderCrmCommentsTable(items) {{
            if (!items || !items.length) {{
                return '<div class="crm-empty">Нет статусов в CRM</div>';
            }}
            const rows = items.map((item) => {{
                const sender = (item.sender || '—').replace(/</g, '&lt;');
                const comment = (item.comment || '—').replace(/</g, '&lt;');
                const created = (item.created_label || '—').replace(/</g, '&lt;');
                return `<tr><td>${{sender}}</td><td>${{comment}}</td><td class="crm-date">${{created}}</td></tr>`;
            }}).join('');
            return `<table class="crm-comments"><thead><tr><th>Отправитель</th><th>Комментарий</th><th>Дата создания</th></tr></thead><tbody>${{rows}}</tbody></table>`;
        }}
        async function loadCrmHistory(detailsEl) {{
            const requestId = detailsEl.dataset.requestId;
            const body = detailsEl.querySelector('.crm-history-body');
            const summary = detailsEl.querySelector('summary');
            if (crmHistoryCache[requestId]) {{
                body.innerHTML = renderCrmCommentsTable(crmHistoryCache[requestId]);
                detailsEl.dataset.loaded = '1';
                summary.textContent = 'История статусов (' + crmHistoryCache[requestId].length + ')';
                return;
            }}
            body.innerHTML = '<div class="crm-loading">Загрузка истории статусов…</div>';
            try {{
                const resp = await fetch('/sla/bank-comments/' + requestId);
                const data = await resp.json();
                if (!resp.ok) throw new Error(data.error || 'Ошибка загрузки');
                const items = data.comments || [];
                crmHistoryCache[requestId] = items;
                body.innerHTML = renderCrmCommentsTable(items);
                detailsEl.dataset.loaded = '1';
                summary.textContent = 'История статусов (' + items.length + ')';
            }} catch (e) {{
                body.innerHTML = '<div class="crm-error">' + (e.message || 'Не удалось загрузить историю') + '</div>';
            }}
        }}
        document.addEventListener('toggle', (event) => {{
            const detailsEl = event.target;
            if (!(detailsEl instanceof HTMLDetailsElement)) return;
            if (!detailsEl.classList.contains('crm-history') || !detailsEl.open) return;
            if (detailsEl.dataset.loaded === '1') return;
            loadCrmHistory(detailsEl);
        }}, true);
    </script>
</body>
</html>'''


@app.route('/sla')
def sla_detail_page():
    """Страница детализации SLA."""
    start_dt, end_dt = parse_sla_period()
    try:
        stages = compute_sla_detail(start_dt, end_dt)
    except Exception as e:
        print(f"❌ Ошибка расчёта SLA detail: {e}")
        return Response(
            f"<h1>Ошибка расчёта SLA</h1><pre>{html.escape(str(e))}</pre>",
            status=500,
            mimetype='text/html; charset=utf-8',
        )

    try:
        heatmap_html = render_heatmap_section(fetch_heatmap_payload(end_dt))
    except Exception as e:
        print(f"❌ Ошибка тепловой карты регионов: {e}")
        heatmap_html = (
            '<section class="heat-wrap" id="region-heatmap">'
            '<h2>Тепловая карта отправок по регионам</h2>'
            '<div class="heat-empty">Не удалось построить карту регионов.</div>'
            '</section>'
        )

    html_content = generate_html(stages, start_dt, end_dt).replace(
        '<!--HEATMAP-->',
        heatmap_html,
        1,
    )
    return Response(html_content, mimetype='text/html; charset=utf-8')


@app.route('/sla/comment', methods=['POST'])
def sla_save_comment():
    """Сохранение внутреннего комментария к этапу заявки."""
    payload = request.get_json(silent=True) or {}
    try:
        request_id = int(payload.get('request_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Некорректный request_id'}), 400

    stage_key = (payload.get('stage_key') or '').strip()
    if not stage_key or stage_key not in {s['key'] for s in SLA_STAGES}:
        return jsonify({'error': 'Некорректный stage_key'}), 400

    comment = payload.get('comment')
    if comment is None:
        comment = ''
    comment = str(comment).strip()

    try:
        upsert_comment(request_id, stage_key, comment)
    except Exception as e:
        print(f"❌ Ошибка сохранения комментария SLA: {e}")
        return jsonify({'error': 'Не удалось сохранить комментарий'}), 500

    return jsonify({'ok': True})


@app.route('/sla/bank-comments/<int:request_id>')
def sla_bank_comments(request_id):
    """История статусов CRM по заявке (как в карточке install)."""
    try:
        token = get_crm_token()
        bundle = fetch_bank_request_bundle(token, request_id)
    except Exception as e:
        print(f"❌ Ошибка чтения истории статусов CRM {request_id}: {e}")
        return jsonify({'error': 'Не удалось загрузить историю статусов'}), 500

    try:
        cache_bank_comments(request_id, bundle)
    except Exception as e:
        print(f"⚠️ Не удалось кэшировать историю статусов {request_id}: {e}")

    return jsonify({
        'ok': True,
        'request_id': request_id,
        'comments': bundle.get('comments') or [],
    })


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)
