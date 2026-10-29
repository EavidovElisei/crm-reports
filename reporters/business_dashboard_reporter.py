#!/usr/bin/env python3
"""
Репортер бизнес-дашборда - показывает ключевые метрики и аналитику
"""
import psycopg2
import json
import requests
import hashlib
import base64
from datetime import datetime, timedelta
from flask import Flask, request, Response
from config import DB_CONFIG, CRM_CONFIG

app = Flask(__name__)

# Конфигурация для analytics API
ANALYTICS_CONFIG = {
    'client_id': CRM_CONFIG.get('client_id', 'cashdesk-rest-client'),
    'client_secret': CRM_CONFIG.get('client_secret', 'cashdesk-rest-client'),
    'grant_type': 'password',
    'auth_url': CRM_CONFIG.get('auth_url', 'https://kassa.bifit.com/admin-api/oauth/token'),
    'analytics_url': CRM_CONFIG.get(
        'analytics_url',
        'https://kassa.bifit.com/admin-api/protected/boxed_kkm/analytics',
    ),
}


def hash_password(password: str) -> str:
    """Хеширование пароля для API"""
    sha256 = hashlib.sha256(password.encode()).digest()
    return base64.b64encode(sha256).decode()


def get_credentials():
    """Получение логина и пароля из БД"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("SELECT login, password FROM auth_tokens WHERE service = 'crm'")
    result = cur.fetchone()

    cur.close()
    conn.close()

    if not result:
        raise ValueError("Не найдены учетные данные для crm")

    return result[0], result[1]


def get_admin_token():
    """Получение токена авторизации для admin-api"""
    username, password = get_credentials()

    payload = {
        "username": username,
        "password": hash_password(password),
        "client_id": ANALYTICS_CONFIG['client_id'],
        "client_secret": ANALYTICS_CONFIG['client_secret'],
        "grant_type": ANALYTICS_CONFIG['grant_type'],
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    response = requests.post(ANALYTICS_CONFIG['auth_url'], data=payload, headers=headers)
    response.raise_for_status()
    return response.json()["access_token"]


def get_analytics_data(min_date=None, max_date=None):
    """Получение данных аналитики"""
    try:
        token = get_admin_token()

        if not min_date or not max_date:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
            min_date = str(int(start_date.timestamp() * 1000))
            max_date = str(int(end_date.timestamp() * 1000))

        params = {
            "min_date": min_date,
            "max_date": max_date
        }
        headers = {"Authorization": f"Bearer {token}"}

        response = requests.get(ANALYTICS_CONFIG['analytics_url'], headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    except Exception as e:
        print(f"❌ Ошибка получения данных аналитики: {e}")
        return None


def get_cached_analytics():
    """Получение кешированных данных аналитики из БД"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        cur.execute("""
            SELECT data, created_at FROM analytics_data
            ORDER BY created_at DESC
            LIMIT 1
        """)

        result = cur.fetchone()
        cur.close()
        conn.close()

        if result and result[0]:
            data = result[0] if isinstance(result[0], dict) else json.loads(result[0])
            return data, result[1]

        return {}, None

    except Exception as e:
        print(f"❌ Ошибка получения кешированных данных: {e}")
        return {}, None


def _ms_to_days(value):
    """Миллисекунды → дни."""
    if value is None:
        return None
    return round(float(value) / 86400000.0, 2)


def parse_sla_period():
    """Период SLA из query start/end (по умолчанию — текущий месяц)."""
    start_param = request.args.get('start')
    end_param = request.args.get('end')

    if start_param and end_param:
        try:
            start_date = datetime.strptime(start_param, '%Y-%m-%d')
            end_date = datetime.strptime(end_param, '%Y-%m-%d')
        except ValueError:
            now = datetime.now()
            start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now
    else:
        now = datetime.now()
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    search_start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    search_end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    return search_start, search_end


def compute_sla_metrics(start_dt=None, end_dt=None):
    """Среднее и медиана SLA по датам заявок за период (дни).

    Период фильтрует заявки по дате создания (created).
    """
    empty = {
        'lead_to_invoice_avg': None,
        'lead_to_invoice_med': None,
        'lead_to_invoice_n': 0,
        'invoice_to_payment_avg': None,
        'invoice_to_payment_med': None,
        'invoice_to_payment_n': 0,
        'payment_to_delivery_avg': None,
        'payment_to_delivery_med': None,
        'payment_to_delivery_n': 0,
        'invoice_to_delivery_avg': None,
        'invoice_to_delivery_med': None,
        'invoice_to_delivery_n': 0,
    }
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        params = []
        period_sql = ""
        if start_dt is not None and end_dt is not None:
            start_ms = int(start_dt.timestamp() * 1000)
            end_ms = int(end_dt.timestamp() * 1000)
            period_sql = """
                AND (data->>'created') ~ '^[0-9]+$'
                AND (data->>'created')::bigint BETWEEN %s AND %s
            """
            params.extend([start_ms, end_ms])

        # Разницы в мс; отсекаем отрицательные и аномалии > 365 дней
        cur.execute(f"""
            WITH diffs AS (
                SELECT
                    CASE
                        WHEN (data->>'incomeSetDate') ~ '^[0-9]+$'
                         AND (data->>'created') ~ '^[0-9]+$'
                         AND ((data->>'incomeSetDate')::bigint - (data->>'created')::bigint)
                             BETWEEN 0 AND 365::bigint * 86400000
                        THEN ((data->>'incomeSetDate')::bigint - (data->>'created')::bigint)
                    END AS lead_ms,
                    CASE
                        WHEN (data->>'incomePaidDate') ~ '^[0-9]+$'
                         AND (data->>'incomeSetDate') ~ '^[0-9]+$'
                         AND ((data->>'incomePaidDate')::bigint - (data->>'incomeSetDate')::bigint)
                             BETWEEN 0 AND 365::bigint * 86400000
                        THEN ((data->>'incomePaidDate')::bigint - (data->>'incomeSetDate')::bigint)
                    END AS invoice_pay_ms,
                    CASE
                        WHEN (data->>'deliveredDate') ~ '^[0-9]+$'
                         AND (data->>'incomePaidDate') ~ '^[0-9]+$'
                         AND ((data->>'deliveredDate')::bigint - (data->>'incomePaidDate')::bigint)
                             BETWEEN 0 AND 365::bigint * 86400000
                        THEN ((data->>'deliveredDate')::bigint - (data->>'incomePaidDate')::bigint)
                    END AS pay_del_ms,
                    CASE
                        WHEN (data->>'deliveredDate') ~ '^[0-9]+$'
                         AND (data->>'incomeSetDate') ~ '^[0-9]+$'
                         AND ((data->>'deliveredDate')::bigint - (data->>'incomeSetDate')::bigint)
                             BETWEEN 0 AND 365::bigint * 86400000
                        THEN ((data->>'deliveredDate')::bigint - (data->>'incomeSetDate')::bigint)
                    END AS invoice_del_ms
                FROM requests
                WHERE data IS NOT NULL
                {period_sql}
            )
            SELECT
                AVG(lead_ms),
                percentile_cont(0.5) WITHIN GROUP (ORDER BY lead_ms),
                COUNT(lead_ms),
                AVG(invoice_pay_ms),
                percentile_cont(0.5) WITHIN GROUP (ORDER BY invoice_pay_ms),
                COUNT(invoice_pay_ms),
                AVG(pay_del_ms),
                percentile_cont(0.5) WITHIN GROUP (ORDER BY pay_del_ms),
                COUNT(pay_del_ms),
                AVG(invoice_del_ms),
                percentile_cont(0.5) WITHIN GROUP (ORDER BY invoice_del_ms),
                COUNT(invoice_del_ms)
            FROM diffs
        """, params)

        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return empty

        return {
            'lead_to_invoice_avg': _ms_to_days(row[0]),
            'lead_to_invoice_med': _ms_to_days(row[1]),
            'lead_to_invoice_n': int(row[2] or 0),
            'invoice_to_payment_avg': _ms_to_days(row[3]),
            'invoice_to_payment_med': _ms_to_days(row[4]),
            'invoice_to_payment_n': int(row[5] or 0),
            'payment_to_delivery_avg': _ms_to_days(row[6]),
            'payment_to_delivery_med': _ms_to_days(row[7]),
            'payment_to_delivery_n': int(row[8] or 0),
            'invoice_to_delivery_avg': _ms_to_days(row[9]),
            'invoice_to_delivery_med': _ms_to_days(row[10]),
            'invoice_to_delivery_n': int(row[11] or 0),
        }

    except Exception as e:
        print(f"❌ Ошибка расчёта SLA: {e}")
        return empty


def format_days(value):
    """Форматирование длительности в днях."""
    if value is None:
        return "—"
    return f"{value:.1f}".replace(".", ",") + " дн."


def format_number(value):
    """Форматирование числа для отображения"""
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        if value == int(value):
            return f"{int(value):,}".replace(",", " ")
        return f"{value:.2f}".replace(".", ",")
    return str(value)


def as_mapping(value, default_label="Всего"):
    """Приводит число или словарь остатков к единому виду {label: count}."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, (int, float)):
        return {default_label: value}
    return {}


def tone_higher_better(value, good, warning):
    """CSS-тон для метрик, где больше = лучше."""
    if value is None:
        return "neutral"
    if value >= good:
        return "good"
    if value >= warning:
        return "warning"
    return "bad"


def tone_lower_better(value, good, warning=None):
    """CSS-тон для метрик, где меньше = лучше. Дольше идеала — невыполнение."""
    if value is None:
        return "neutral"
    if value <= good + 1e-9:
        return "good"
    if warning is not None and warning > good and value <= warning:
        return "warning"
    return "bad"


def render_metric_rows(items, value_formatter=None):
    """HTML-строки метрик из словаря."""
    if not items:
        return '<div class="empty">Нет данных</div>'

    rows = []
    for label, count in items.items():
        numeric = count if isinstance(count, (int, float)) else None
        tone = "neutral"
        if numeric is not None:
            tone = tone_higher_better(numeric, 10, 5)
        display = value_formatter(count) if value_formatter else format_number(count)
        rows.append(
            f'<div class="metric"><span class="metric-label">{label}</span>'
            f'<span class="metric-value {tone}">{display}</span></div>'
        )
    return "".join(rows)


def generate_html_template(data):
    """Генерация HTML шаблона с данными"""
    alerts_html = ""
    for alert in data['alerts']:
        alert_class = alert['type']
        title = {
            'critical': 'Критично',
            'warning': 'Внимание',
            'info': 'Информация',
        }.get(alert_class, 'Информация')
        alerts_html += (
            f'<div class="alert {alert_class}">'
            f'<strong>{title}:</strong> {alert["message"]}</div>'
        )

    if not alerts_html:
        alerts_html = (
            '<div class="alert info"><strong>Информация:</strong> '
            'Все системы работают нормально</div>'
        )

    sim_remains_html = render_metric_rows(data['sim_remains'])
    fn_ready_html = render_metric_rows(data['fn15_ready'])
    fn36_rows = render_metric_rows(data['fn36_ready'])

    return f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Дашборд контроля бизнес-процессов</title>
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

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
            background:
                radial-gradient(circle at top left, rgba(37, 99, 235, 0.08), transparent 40%),
                linear-gradient(180deg, #f7f9fc 0%, var(--bg) 100%);
            color: var(--text);
            line-height: 1.5;
            min-height: 100vh;
        }}

        .header {{
            background: linear-gradient(135deg, var(--header-from) 0%, var(--header-to) 100%);
            color: white;
            padding: 28px 20px 36px;
        }}

        .header-inner {{
            max-width: 1280px;
            margin: 0 auto;
        }}

        .header-top {{
            display: flex;
            justify-content: space-between;
            gap: 16px;
            align-items: flex-start;
            flex-wrap: wrap;
            margin-bottom: 24px;
        }}

        .header h1 {{
            font-size: 1.9rem;
            font-weight: 650;
            letter-spacing: -0.02em;
        }}

        .header p {{
            opacity: 0.85;
            margin-top: 6px;
        }}

        .header-actions {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }}

        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 14px;
            border-radius: 10px;
            text-decoration: none;
            font-size: 0.92rem;
            font-weight: 600;
            border: 1px solid rgba(255,255,255,0.25);
            color: white;
            background: rgba(255,255,255,0.12);
            transition: background 0.2s ease, transform 0.2s ease;
        }}

        .btn:hover {{
            background: rgba(255,255,255,0.2);
            transform: translateY(-1px);
        }}

        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 14px;
        }}

        .kpi {{
            background: rgba(255,255,255,0.12);
            border: 1px solid rgba(255,255,255,0.18);
            border-radius: 16px;
            padding: 18px;
            backdrop-filter: blur(8px);
        }}

        .kpi-label {{
            font-size: 0.85rem;
            opacity: 0.8;
            margin-bottom: 8px;
        }}

        .kpi-value {{
            font-size: 2rem;
            font-weight: 700;
            letter-spacing: -0.03em;
        }}

        .kpi-sub {{
            margin-top: 6px;
            font-size: 0.82rem;
            opacity: 0.75;
        }}

        .container {{
            max-width: 1280px;
            margin: -18px auto 0;
            padding: 0 20px 40px;
        }}

        .alerts {{
            background: var(--card);
            border-radius: 16px;
            padding: 18px 20px;
            margin-bottom: 18px;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
        }}

        .alerts h2 {{
            font-size: 1.05rem;
            margin-bottom: 12px;
        }}

        .alert {{
            padding: 12px 14px;
            border-radius: 10px;
            margin-bottom: 8px;
            border-left: 4px solid;
        }}

        .alert:last-child {{
            margin-bottom: 0;
        }}

        .alert.critical {{
            background: #fef2f2;
            border-color: var(--bad);
            color: #991b1b;
        }}

        .alert.warning {{
            background: #fffbeb;
            border-color: var(--warning);
            color: #92400e;
        }}

        .alert.info {{
            background: #eff6ff;
            border-color: var(--accent);
            color: #1e40af;
        }}

        .dashboard-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 16px;
        }}

        .card {{
            background: var(--card);
            border-radius: 16px;
            padding: 20px;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
            border: 1px solid rgba(226, 232, 240, 0.9);
        }}

        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--line);
        }}

        .card-title {{
            font-size: 1.05rem;
            font-weight: 650;
        }}

        .status-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--good);
        }}

        .status-dot.warning {{ background: var(--warning); }}
        .status-dot.bad {{ background: var(--bad); }}

        .metric {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            padding: 10px 0;
            border-bottom: 1px solid var(--line);
        }}

        .metric:last-child {{
            border-bottom: none;
        }}

        .metric-label {{
            color: var(--muted);
            font-size: 0.92rem;
        }}

        .metric-value {{
            font-weight: 700;
            font-size: 1.05rem;
            white-space: nowrap;
        }}

        .metric-value.good {{ color: var(--good); }}
        .metric-value.warning {{ color: var(--warning); }}
        .metric-value.bad {{ color: var(--bad); }}
        .metric-value.neutral {{ color: var(--text); }}

        .empty {{
            color: var(--muted);
            padding: 8px 0;
        }}

        .section-split {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }}

        .section-split h3 {{
            font-size: 0.85rem;
            color: var(--muted);
            margin-bottom: 4px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}

        .footer-note {{
            margin-top: 16px;
            color: var(--muted);
            font-size: 0.85rem;
            text-align: center;
        }}

        .sla-section {{
            background: var(--card);
            border-radius: 16px;
            padding: 18px 20px;
            margin-bottom: 18px;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
            border: 1px solid rgba(226, 232, 240, 0.9);
        }}

        .sla-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 16px;
            flex-wrap: wrap;
            margin-bottom: 14px;
        }}

        .sla-section h2 {{
            font-size: 1.05rem;
            margin-bottom: 4px;
        }}

        .sla-period-note {{
            color: var(--muted);
            font-size: 0.85rem;
        }}

        .sla-period-form {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
        }}

        .sla-period-form label {{
            color: var(--muted);
            font-size: 0.85rem;
        }}

        .sla-period-form input[type="date"] {{
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 7px 10px;
            font-size: 0.9rem;
            color: var(--text);
            background: #fff;
        }}

        .sla-period-form button,
        .sla-quick button {{
            border: 1px solid var(--line);
            background: #f8fafc;
            color: var(--text);
            border-radius: 8px;
            padding: 7px 12px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
        }}

        .sla-period-form button.primary {{
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
        }}

        .sla-detail-link {{
            display: inline-flex;
            align-items: center;
            padding: 7px 12px;
            border-radius: 8px;
            background: #eff6ff;
            color: var(--accent);
            text-decoration: none;
            font-size: 0.85rem;
            font-weight: 650;
            border: 1px solid #bfdbfe;
        }}

        .sla-quick {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 14px;
        }}

        .sla-grid {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 12px;
        }}

        .sla-card {{
            background: #f8fafc;
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 16px;
            border-top: 3px solid var(--accent);
        }}

        .sla-card.good {{ border-top-color: var(--good); }}
        .sla-card.warning {{ border-top-color: var(--warning); }}
        .sla-card.bad {{ border-top-color: var(--bad); }}

        .sla-label {{
            color: var(--muted);
            font-size: 0.88rem;
            margin-bottom: 8px;
            min-height: 2.4em;
        }}

        .sla-value {{
            font-size: 1.7rem;
            font-weight: 700;
            letter-spacing: -0.03em;
            color: var(--text);
        }}

        .sla-value.good {{ color: var(--good); }}
        .sla-value.warning {{ color: var(--warning); }}
        .sla-value.bad {{ color: var(--bad); }}

        .sla-value-caption {{
            font-size: 0.75rem;
            color: var(--muted);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            margin-bottom: 2px;
        }}

        .sla-stats {{
            margin-top: 10px;
            display: grid;
            gap: 4px;
        }}

        .sla-stat-row {{
            display: flex;
            justify-content: space-between;
            gap: 8px;
            font-size: 0.84rem;
            color: var(--muted);
        }}

        .sla-stat-row strong {{
            color: var(--text);
            font-weight: 650;
        }}

        .sla-sub {{
            margin-top: 6px;
            color: var(--muted);
            font-size: 0.8rem;
        }}

        @media (max-width: 960px) {{
            .kpi-grid,
            .dashboard-grid,
            .section-split,
            .sla-grid {{
                grid-template-columns: 1fr;
            }}

            .kpi-value {{
                font-size: 1.6rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <div class="header-top">
                <div>
                    <h1>Контроль бизнес-процессов</h1>
                    <p>Последнее обновление: {data['current_time']}</p>
                </div>
                <div class="header-actions">
                    <a class="btn" href="/">На главную</a>
                    <a class="btn" href="/sla">Детализация SLA</a>
                    <a class="btn" href="/inventory">Инвентаризация</a>
                    <a class="btn" href="/api/analytics">JSON API</a>
                    <a class="btn" href="/report">Отчёт CRM</a>
                </div>
            </div>
            <div class="kpi-grid">
                <div class="kpi">
                    <div class="kpi-label">Заявок за период</div>
                    <div class="kpi-value">{data['metrics']['requests_per_period']}</div>
                    <div class="kpi-sub">За неделю: {data['metrics']['requests_per_week']}</div>
                </div>
                <div class="kpi">
                    <div class="kpi-label">Конверсия</div>
                    <div class="kpi-value">{data['metrics']['conversion']}</div>
                    <div class="kpi-sub">Средняя оплата: {data['metrics']['avg_payment_time']}</div>
                </div>
                <div class="kpi">
                    <div class="kpi-label">Очередь регистрации</div>
                    <div class="kpi-value">{data['metrics']['registration_queue']}</div>
                    <div class="kpi-sub">Регистрация: {data['metrics']['avg_registration_time']}</div>
                </div>
                <div class="kpi">
                    <div class="kpi-label">Остаток ФН</div>
                    <div class="kpi-value">{data['metrics']['fn_remains']}</div>
                    <div class="kpi-sub">Расход/нед.: {data['metrics']['fn_week_consumption']}</div>
                </div>
            </div>
        </div>
    </div>

    <div class="container">
        <div class="sla-section">
            <div class="sla-header">
                <div>
                    <h2>SLA по этапам</h2>
                    <div class="sla-period-note">
                        Период по дате создания заявки:
                        {data['sla']['period_label']}
                    </div>
                </div>
                <form class="sla-period-form" method="GET" action="/dashboard">
                    <label for="sla-start">с</label>
                    <input id="sla-start" type="date" name="start" value="{data['sla']['start']}" required>
                    <label for="sla-end">по</label>
                    <input id="sla-end" type="date" name="end" value="{data['sla']['end']}" required>
                    <button class="primary" type="submit">Показать</button>
                    <a class="sla-detail-link" href="/sla?start={data['sla']['start']}&amp;end={data['sla']['end']}">Детализация SLA →</a>
                </form>
            </div>
            <div class="sla-quick">
                <button type="button" onclick="setSlaPeriod(7)">7 дней</button>
                <button type="button" onclick="setSlaPeriod(30)">30 дней</button>
                <button type="button" onclick="setSlaPeriod('month')">Этот месяц</button>
                <button type="button" onclick="setSlaPeriod(90)">90 дней</button>
            </div>
            <div class="sla-grid">
                <div class="sla-card {data['sla']['lead_to_invoice_tone']}">
                    <div class="sla-label">Заявка → выставление счёта</div>
                    <div class="sla-sub">Идеал ≤ 1 дн.</div>
                    <div class="sla-value-caption">Медиана</div>
                    <div class="sla-value {data['sla']['lead_to_invoice_tone']}">{data['sla']['lead_to_invoice_med']}</div>
                    <div class="sla-stats">
                        <div class="sla-stat-row"><span>Среднее</span><strong>{data['sla']['lead_to_invoice_avg']}</strong></div>
                        <div class="sla-stat-row"><span>Выборка</span><strong>n={data['sla']['lead_to_invoice_n']}</strong></div>
                    </div>
                </div>
                <div class="sla-card {data['sla']['invoice_to_payment_tone']}">
                    <div class="sla-label">Счёт → оплата</div>
                    <div class="sla-sub">Идеал ≤ 1 дн.</div>
                    <div class="sla-value-caption">Медиана</div>
                    <div class="sla-value {data['sla']['invoice_to_payment_tone']}">{data['sla']['invoice_to_payment_med']}</div>
                    <div class="sla-stats">
                        <div class="sla-stat-row"><span>Среднее</span><strong>{data['sla']['invoice_to_payment_avg']}</strong></div>
                        <div class="sla-stat-row"><span>Выборка</span><strong>n={data['sla']['invoice_to_payment_n']}</strong></div>
                    </div>
                </div>
                <div class="sla-card {data['sla']['payment_to_delivery_tone']}">
                    <div class="sla-label">Оплата → доставка</div>
                    <div class="sla-sub">Идеал ≤ 7 дн. (отправка 1 + доставка 6)</div>
                    <div class="sla-value-caption">Медиана</div>
                    <div class="sla-value {data['sla']['payment_to_delivery_tone']}">{data['sla']['payment_to_delivery_med']}</div>
                    <div class="sla-stats">
                        <div class="sla-stat-row"><span>Среднее</span><strong>{data['sla']['payment_to_delivery_avg']}</strong></div>
                        <div class="sla-stat-row"><span>Выборка</span><strong>n={data['sla']['payment_to_delivery_n']}</strong></div>
                    </div>
                </div>
                <div class="sla-card {data['sla']['invoice_to_delivery_tone']}">
                    <div class="sla-label">Счёт → доставка (общее)</div>
                    <div class="sla-sub">Идеал ≤ 8 дн. (оплата 1 + отправка 1 + доставка 6)</div>
                    <div class="sla-value-caption">Медиана</div>
                    <div class="sla-value {data['sla']['invoice_to_delivery_tone']}">{data['sla']['invoice_to_delivery_med']}</div>
                    <div class="sla-stats">
                        <div class="sla-stat-row"><span>Среднее</span><strong>{data['sla']['invoice_to_delivery_avg']}</strong></div>
                        <div class="sla-stat-row"><span>Выборка</span><strong>n={data['sla']['invoice_to_delivery_n']}</strong></div>
                    </div>
                </div>
            </div>
        </div>

        <div class="alerts">
            <h2>Уведомления</h2>
            {alerts_html}
        </div>

        <div class="dashboard-grid">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Производство</div>
                    <div class="status-dot {data['tones']['production']}"></div>
                </div>
                <div class="metric">
                    <span class="metric-label">Время регистрации</span>
                    <span class="metric-value {data['tones']['avg_registration_time']}">{data['metrics']['avg_registration_time']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Время доставки</span>
                    <span class="metric-value good">{data['metrics']['avg_delivery_time']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Очередь регистрации</span>
                    <span class="metric-value {data['tones']['registration_queue']}">{data['metrics']['registration_queue']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Остаток ФН</span>
                    <span class="metric-value {data['tones']['fn_remains']}">{data['metrics']['fn_remains']}</span>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">Складские остатки</div>
                    <div class="status-dot"></div>
                </div>
                <div class="metric">
                    <span class="metric-label">Остаток ККМ 405</span>
                    <span class="metric-value good">{data['metrics']['remains_kkm405']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Остаток ОФД 405</span>
                    <span class="metric-value {'warning' if data['metrics']['remains_ofd405'] in ('0', '—') else 'good'}">{data['metrics']['remains_ofd405']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Потребление ФН / неделю</span>
                    <span class="metric-value neutral">{data['metrics']['fn_week_consumption']}</span>
                </div>
                <div class="section-split" style="margin-top: 12px;">
                    <div>
                        <h3>Готовые ФН15</h3>
                        {fn_ready_html}
                    </div>
                    <div>
                        <h3>Готовые ФН36</h3>
                        {fn36_rows}
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">Остатки SIM-карт</div>
                    <div class="status-dot"></div>
                </div>
                {sim_remains_html}
            </div>

            <div class="card">
                <div class="card-header">
                    <div class="card-title">Автокомплекты</div>
                    <div class="status-dot"></div>
                </div>
                <div class="metric">
                    <span class="metric-label">В доставке за неделю</span>
                    <span class="metric-value good">{data['metrics']['auto_kit_delivery_week']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">В доставке за месяц</span>
                    <span class="metric-value good">{data['metrics']['auto_kit_delivery_month']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Доставлено за месяц</span>
                    <span class="metric-value good">{data['metrics']['auto_kit_delivered_month']}</span>
                </div>
            </div>
        </div>

        <div class="footer-note">
            Данные аналитики admin-api · сырой JSON: <a href="/api/analytics">/api/analytics</a>
        </div>
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
            document.querySelector('.sla-period-form').submit();
        }}
    </script>
</body>
</html>'''


@app.route('/dashboard')
def business_dashboard():
    """Главная страница бизнес-дашборда"""

    analytics, cached_at = get_cached_analytics()

    if not analytics:
        analytics = get_analytics_data() or {}
        cached_at = None

    sla_start, sla_end = parse_sla_period()
    sla_raw = compute_sla_metrics(sla_start, sla_end)

    requests_per_period = analytics.get('requestsPerPeriod', 0)
    requests_per_week = analytics.get('requestsPerWeek', 0)
    conversion = analytics.get('conversion', 0) or 0
    avg_payment_time = analytics.get('averagePaymentTime', 0) or 0
    avg_registration_time = analytics.get('averageRegistrationTime', 0) or 0
    avg_delivery_time = analytics.get('averageDeliveryTime', 0) or 0
    registration_queue = analytics.get('registrationQueue', 0) or 0
    fn_remains = analytics.get('fnRemains', 0) or 0
    fn_week_consumption = analytics.get('fnWeekConsumption', 0) or 0
    remains_kkm405 = analytics.get('remainsKkm405', 0) or 0
    remains_sim405 = as_mapping(analytics.get('remainsSim405', {}))
    remains_ofd405 = analytics.get('remainsOfd405', 0) or 0
    ready_fn15 = as_mapping(analytics.get('readyFn15', {}), "Готово")
    ready_fn36 = as_mapping(analytics.get('readyFn36', {}), "Готово")
    auto_kit_delivery_week = analytics.get('autoKitInDeliveryPerWeek', 0) or 0
    auto_kit_delivery_month = analytics.get('autoKitInDeliveryPerMonth', 0) or 0
    auto_kit_delivered_month = analytics.get('autoKitDeliveredPerMonth', 0) or 0

    alerts = []

    if registration_queue > 20:
        alerts.append({
            'type': 'critical',
            'message': f'Критическая очередь на регистрацию: {registration_queue} заявок'
        })
    elif registration_queue > 10:
        alerts.append({
            'type': 'warning',
            'message': f'Повышенная очередь на регистрацию: {registration_queue} заявок'
        })

    if fn_remains < 50:
        alerts.append({
            'type': 'critical',
            'message': f'Критически низкий остаток ФН: {fn_remains} шт.'
        })
    elif fn_remains < 100:
        alerts.append({
            'type': 'warning',
            'message': f'Низкий остаток ФН: {fn_remains} шт.'
        })

    if conversion < 0.7:
        alerts.append({
            'type': 'warning',
            'message': f'Низкая конверсия: {conversion:.1%}'
        })

    if avg_registration_time > 20:
        alerts.append({
            'type': 'warning',
            'message': f'Долгое время регистрации: {avg_registration_time:.1f} дней'
        })

    conversion_tone = tone_higher_better(conversion, 0.8, 0.7)
    queue_tone = tone_lower_better(registration_queue, 5, 10)
    fn_tone = tone_higher_better(fn_remains, 100, 50)
    reg_time_tone = tone_lower_better(avg_registration_time, 10, 15)
    production_tone = 'bad' if 'bad' in (queue_tone, reg_time_tone, fn_tone) else (
        'warning' if 'warning' in (queue_tone, reg_time_tone, fn_tone) else 'good'
    )

    if cached_at:
        current_time = cached_at.strftime('%d.%m.%Y %H:%M')
    else:
        current_time = datetime.now().strftime('%d.%m.%Y %H:%M')

    template_data = {
        'current_time': current_time,
        'alerts': alerts,
        'sla': {
            'start': sla_start.strftime('%Y-%m-%d'),
            'end': sla_end.strftime('%Y-%m-%d'),
            'period_label': (
                f"{sla_start.strftime('%d.%m.%Y')} — {sla_end.strftime('%d.%m.%Y')}"
            ),
            'lead_to_invoice_med': format_days(sla_raw['lead_to_invoice_med']),
            'lead_to_invoice_avg': format_days(sla_raw['lead_to_invoice_avg']),
            'lead_to_invoice_n': format_number(sla_raw['lead_to_invoice_n']),
            'lead_to_invoice_tone': tone_lower_better(sla_raw['lead_to_invoice_med'], 1.0),
            'invoice_to_payment_med': format_days(sla_raw['invoice_to_payment_med']),
            'invoice_to_payment_avg': format_days(sla_raw['invoice_to_payment_avg']),
            'invoice_to_payment_n': format_number(sla_raw['invoice_to_payment_n']),
            'invoice_to_payment_tone': tone_lower_better(sla_raw['invoice_to_payment_med'], 1.0),
            'payment_to_delivery_med': format_days(sla_raw['payment_to_delivery_med']),
            'payment_to_delivery_avg': format_days(sla_raw['payment_to_delivery_avg']),
            'payment_to_delivery_n': format_number(sla_raw['payment_to_delivery_n']),
            'payment_to_delivery_tone': tone_lower_better(sla_raw['payment_to_delivery_med'], 7.0),
            'invoice_to_delivery_med': format_days(sla_raw['invoice_to_delivery_med']),
            'invoice_to_delivery_avg': format_days(sla_raw['invoice_to_delivery_avg']),
            'invoice_to_delivery_n': format_number(sla_raw['invoice_to_delivery_n']),
            'invoice_to_delivery_tone': tone_lower_better(sla_raw['invoice_to_delivery_med'], 8.0),
        },
        'metrics': {
            'requests_per_period': format_number(requests_per_period),
            'requests_per_week': format_number(requests_per_week),
            'conversion': f"{conversion:.1%}" if conversion else "—",
            'avg_payment_time': f"{avg_payment_time:.1f} дн." if avg_payment_time else "—",
            'avg_registration_time': f"{avg_registration_time:.1f} дн." if avg_registration_time else "—",
            'avg_delivery_time': f"{avg_delivery_time:.1f} дн." if avg_delivery_time else "—",
            'registration_queue': format_number(registration_queue),
            'fn_remains': format_number(fn_remains),
            'fn_week_consumption': format_number(fn_week_consumption),
            'remains_kkm405': format_number(remains_kkm405),
            'remains_ofd405': format_number(remains_ofd405),
            'auto_kit_delivery_week': format_number(auto_kit_delivery_week),
            'auto_kit_delivery_month': format_number(auto_kit_delivery_month),
            'auto_kit_delivered_month': format_number(auto_kit_delivered_month)
        },
        'sim_remains': remains_sim405,
        'fn15_ready': ready_fn15,
        'fn36_ready': ready_fn36,
        'tones': {
            'conversion': conversion_tone,
            'registration_queue': queue_tone,
            'fn_remains': fn_tone,
            'avg_registration_time': reg_time_tone,
            'production': production_tone,
        }
    }

    html_content = generate_html_template(template_data)
    return Response(html_content, mimetype='text/html; charset=utf-8')


@app.route('/api/analytics')
def api_analytics():
    """API эндпоинт для получения данных аналитики"""
    min_date = request.args.get('min_date')
    max_date = request.args.get('max_date')

    analytics = get_analytics_data(min_date, max_date)

    # Без дат — пробуем кеш, если live API недоступен
    if not analytics and not min_date and not max_date:
        analytics, _ = get_cached_analytics()

    if analytics:
        return Response(
            json.dumps(analytics, ensure_ascii=False, indent=2),
            mimetype='application/json; charset=utf-8'
        )

    return Response(
        json.dumps({'error': 'Не удалось получить данные аналитики'}, ensure_ascii=False),
        status=500,
        mimetype='application/json; charset=utf-8'
    )


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
