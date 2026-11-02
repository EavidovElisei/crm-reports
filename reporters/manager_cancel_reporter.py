#!/usr/bin/env python3
"""
Причины отмен заявок по менеджерам: динамика по месяцам.

Страница /managers/cancel показывает по каждому менеджеру таблицу
«Причина → доля отмен в месяце», а сверху — сводку по объёму заявок
и проценту отмен, чтобы было видно: выросли отмены в штуках
или выросла их доля.

Отмена относится к месяцу по дате отмены (cancelledDate), а не создания.
"""
import html
import io
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import psycopg2
from flask import Flask, Response, request
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from config import DB_CONFIG, MANAGER_NAMES
from lib.cancel_reasons import classify, percent, reason_rows
from last_comment import get_last_comment_plain

app = Flask(__name__)

CANCELED_STATUSES = ('CANCELED_BY_CLIENT', 'CANCELED_BY_BANK', 'REFUND')
IN_PROGRESS_STATUSES = (
    'NEW', 'DRAFT', 'INCOME_CREATED', 'INCOME_PAID', 'INCOME_PARTIALLY_PAID', 'KKT_LINKED',
)

MONTH_NAMES = [
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]


# --------------------------------------------------------------------------
# Период
# --------------------------------------------------------------------------
def parse_period():
    """Период из query start/end; по умолчанию — 3 последних полных месяца."""
    start_param = request.args.get('start')
    end_param = request.args.get('end')
    if start_param and end_param:
        try:
            return (
                datetime.strptime(start_param, '%Y-%m-%d'),
                datetime.strptime(end_param, '%Y-%m-%d').replace(
                    hour=23, minute=59, second=59, microsecond=999000
                ),
            )
        except ValueError:
            pass

    today = datetime.now()
    first_of_month = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Текущий месяц ещё не закрыт — берём три полностью прошедших
    start = (first_of_month - timedelta(days=1)).replace(day=1)
    start = (start - timedelta(days=1)).replace(day=1)
    start = (start - timedelta(days=1)).replace(day=1)
    end = first_of_month - timedelta(days=1)
    return start, end.replace(hour=23, minute=59, second=59, microsecond=999000)


def month_keys(start_dt, end_dt):
    """Список месяцев периода в виде YYYY-MM."""
    result = []
    cursor = start_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor <= end_dt:
        result.append(cursor.strftime('%Y-%m'))
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return result


def month_label(key, short_year=False):
    year, month = key.split('-')
    name = MONTH_NAMES[int(month) - 1]
    return name if short_year else f'{name} {year}'


# --------------------------------------------------------------------------
# Данные
# --------------------------------------------------------------------------
def load_cancellations(start_dt, end_dt):
    """Отмены за период: менеджер, месяц отмены, причина."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT data->>'callCenterManagerId',
               to_char(to_timestamp(NULLIF(data->>'cancelledDate', '')::bigint / 1000), 'YYYY-MM'),
               data
        FROM requests
        WHERE COALESCE(data->>'cancelledDate', '') ~ '^[0-9]+$'
          AND data->>'status' = ANY(%s)
          AND to_timestamp(NULLIF(data->>'cancelledDate', '')::bigint / 1000) BETWEEN %s AND %s
        """,
        (list(CANCELED_STATUSES), start_dt, end_dt),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def manager_name(raw):
    if raw and str(raw).isdigit():
        return MANAGER_NAMES.get(int(raw)) or f'Менеджер {raw}'
    return 'Без менеджера'


def build_report(start_dt, end_dt):
    """Сводка по менеджерам + таблицы причин по месяцам."""
    months = month_keys(start_dt, end_dt)
    cancellations = load_cancellations(start_dt, end_dt)

    reasons = defaultdict(lambda: defaultdict(Counter))   # менеджер → месяц → причина
    for manager, month, data in cancellations:
        if month not in months:
            continue
        reasons[manager_name(manager)][month][classify(get_last_comment_plain(data) or '')] += 1

    totals = {month: sum(sum(per_month[month].values()) for per_month in reasons.values()) for month in months}
    result = []
    for name, per_month in reasons.items():
        by_month = {month: sum(per_month[month].values()) for month in months}
        rows = []
        for reason in sorted(
            {reason for month in months for reason in per_month[month]},
            key=lambda r: -sum(per_month[m][r] for m in months),
        ):
            rows.append({
                'reason': reason,
                'pct': {m: percent(per_month[m][reason], by_month[m]) for m in months},
                'count': {m: per_month[m][reason] for m in months},
            })
        result.append({
            'name': name,
            'by_month': by_month,
            'total': sum(by_month.values()),
            'reasons': rows,
        })
    result.sort(key=lambda item: -item['total'])
    return result, months, totals


# --------------------------------------------------------------------------
# Оформление
# --------------------------------------------------------------------------
def format_iso(value):
    return value.strftime('%Y-%m-%d')


def render_html(report, months, totals, start_dt, end_dt):
    same_year = len({m.split('-')[0] for m in months}) == 1
    month_headers = ''.join(
        f'<th>{html.escape(month_label(m, same_year))}</th>' for m in months
    )

    summary_rows = []
    for item in report:
        cells = ''.join(f'<td>{item["by_month"][m] or "—"}</td>' for m in months)
        summary_rows.append(
            f'<tr><td>{html.escape(item["name"])}</td>{cells}'
            f'<td class="total">{item["total"]}</td></tr>'
        )

    cards = []
    for item in report:
        body = []
        for row in item['reasons']:
            cells = ''.join(
                f'<td>{row["pct"][m]:.0f}%</td>' if row['count'][m] else '<td class="muted">—</td>'
                for m in months
            )
            body.append(f'<tr><td>{html.escape(row["reason"])}</td>{cells}</tr>')
        footer = ''.join(f'<td>{item["by_month"][m] or 0}</td>' for m in months)
        breakdown = ' / '.join(str(item['by_month'][m] or 0) for m in months)
        cards.append(f'''
        <section class="card">
            <div class="card-head">
                <h2>{html.escape(item['name'])}</h2>
                <span class="count">Отмен: {item['total']} ({breakdown})</span>
            </div>
            <table class="reasons">
                <thead><tr><th>Причина</th>{month_headers}</tr></thead>
                <tbody>{''.join(body)}
                    <tr class="footer"><td>Всего отмен, шт.</td>{footer}</tr>
                </tbody>
            </table>
        </section>''')

    return f'''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Причины отмен по менеджерам</title>
<style>
    :root {{ --green:#1f5c4a; --green-dark:#164236; --line:#e3e8e6; --muted:#8a9995; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:#eef2f1; color:#1d2b28;
        font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif; }}
    header.header {{ background:linear-gradient(135deg,var(--green) 0%,var(--green-dark) 100%);
        color:#fff; padding:18px 26px 20px; }}
    .header-top {{ display:flex; justify-content:space-between; align-items:flex-start;
        gap:16px; flex-wrap:wrap; }}
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
    .page {{ padding:18px 26px 40px; display:flex; flex-direction:column; gap:18px; }}
    .card {{ background:#fff; border-radius:12px; box-shadow:0 2px 10px rgba(0,0,0,.06);
        padding:16px 18px 18px; }}
    .card-head {{ display:flex; justify-content:space-between; align-items:baseline;
        gap:12px; margin-bottom:12px; flex-wrap:wrap; }}
    h2 {{ margin:0; font-size:17px; }}
    .count {{ font-size:12px; color:var(--muted); }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    thead th {{ text-align:left; padding:10px 12px; color:#6b7a76; font-weight:600;
        border-bottom:1px solid var(--line); background:#f7f9f8; }}
    tbody td {{ padding:11px 12px; border-bottom:1px solid #f0f3f2; }}
    tbody td:not(:first-child), thead th:not(:first-child) {{ width:16%; text-align:left; }}
    tr.footer td {{ font-weight:700; color:#3a4a46; border-bottom:none; }}
    td.muted {{ color:#c3ccc9; }}
    .summary table tbody td:first-child {{ font-weight:600; }}
    .summary td.total {{ font-weight:700; }}
    .note {{ margin:0 2px; color:var(--muted); font-size:12px; line-height:1.6; }}
</style>
</head>
<body>
<header class="header">
    <div class="header-top">
        <div>
            <h1>Причины отмен по менеджерам</h1>
            <div class="sub">Отмены по дате отмены: {start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}</div>
        </div>
        <div class="actions">
            <a class="btn" href="/sla/bank">SLA для банка</a>
            <a class="btn" href="/dashboard">Дашборд</a>
            <a class="btn" href="/">На главную</a>
        </div>
    </div>
    <form class="period" method="GET" action="/managers/cancel">
        <label>Отмены с<input type="date" name="start" value="{format_iso(start_dt)}" required></label>
        <label>по<input type="date" name="end" value="{format_iso(end_dt)}" required></label>
        <button class="primary" type="submit">Показать</button>
        <button class="export" type="button" id="export-xlsx">Выгрузить Excel</button>
    </form>
</header>
<main class="page">
    <section class="card summary">
        <div class="card-head">
            <h2>Сколько отмен у каждого менеджера</h2>
            <span class="count">Всего отмен за период: {sum(totals.values())}</span>
        </div>
        <table>
            <thead><tr><th>Менеджер</th>{month_headers}<th>Итого</th></tr></thead>
            <tbody>{''.join(summary_rows)}</tbody>
        </table>
    </section>
    {''.join(cards)}
    <p class="note">Доли считаются от числа отмен этого менеджера в этом месяце — по строке сумма 100%.
        Причина берётся из комментария к отмене в CRM: если в нём только номер тикета
        или «Отказ клиента» без пояснения, причина попадает в «Причина не указана».
        При малом числе отмен проценты шумят — смотрите строку «Всего отмен, шт.».</p>
</main>
<script>
    document.getElementById('export-xlsx').addEventListener('click', function () {{
        const params = new URLSearchParams(window.location.search);
        const start = params.get('start') || '{format_iso(start_dt)}';
        const end = params.get('end') || '{format_iso(end_dt)}';
        window.location.href = '/managers/cancel/export?start=' + encodeURIComponent(start)
            + '&end=' + encodeURIComponent(end);
    }});
</script>
</body>
</html>'''


def build_xlsx(report, months, start_dt, end_dt):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Причины отмен'

    same_year = len({m.split('-')[0] for m in months}) == 1
    header = (['Менеджер', 'Причина']
              + [month_label(m, same_year) for m in months] + ['Всего, шт.'])
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical='center')

    grey = PatternFill('solid', fgColor='F2F5F4')
    for item in report:
        for row in item['reasons']:
            ws.append(
                [item['name'], row['reason']]
                + [round(row['pct'][m] / 100, 4) for m in months]
                + [sum(row['count'].values())]
            )
        total_row = ['', 'Всего отмен, шт.'] + [item['by_month'][m] for m in months] + [item['total']]
        ws.append(total_row)
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
            cell.fill = grey

    for row in ws.iter_rows(min_row=2, min_col=3, max_col=2 + len(months)):
        for cell in row:
            if isinstance(cell.value, (int, float)):
                cell.number_format = '0.0%' if cell.column <= 2 + len(months) and cell.row <= ws.max_row else '0'
    # проценты — только в строках причин, в строке «Всего» остаются штуки
    for r in range(2, ws.max_row + 1):
        if str(ws.cell(r, 2).value or '').startswith('Всего отмен'):
            for c in range(3, 3 + len(months)):
                ws.cell(r, c).number_format = '0'

    widths = [18, 34] + [14] * len(months) + [12]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + index) if index <= 26 else 'A'].width = width
    ws.freeze_panes = 'C2'

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


@app.route('/managers/cancel')
def managers_cancel_page():
    """Причины отмен по менеджерам с динамикой по месяцам."""
    start_dt, end_dt = parse_period()
    try:
        report, months, totals = build_report(start_dt, end_dt)
    except Exception as exc:
        return Response(
            f'<h1>Ошибка расчёта отмен</h1><pre>{html.escape(str(exc))}</pre>',
            status=500,
            mimetype='text/html; charset=utf-8',
        ), 500
    return Response(
        render_html(report, months, totals, start_dt, end_dt),
        mimetype='text/html; charset=utf-8',
    )


@app.route('/managers/cancel/export')
def managers_cancel_export():
    """Excel с причинами отмен по менеджерам."""
    start_dt, end_dt = parse_period()
    report, months, _totals = build_report(start_dt, end_dt)
    output = build_xlsx(report, months, start_dt, end_dt)
    filename = f'otmeny_{format_iso(start_dt)}_{format_iso(end_dt)}.xlsx'
    return Response(
        output.read(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5004)