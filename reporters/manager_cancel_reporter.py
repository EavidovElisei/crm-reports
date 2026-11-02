#!/usr/bin/env python3
"""
Причины отмен заявок по менеджерам: динамика по месяцам.

Отмены разделены на два независимых блока, потому что это разные процессы
и разные источники причины:
  • отмены клиентом  (CANCELED_BY_CLIENT) — наши причины, их фиксируем мы;
  • отмены банком    (CANCELED_BY_BANK)   — причины приходят со стороны банка.

Возвраты (REFUND) считаются отдельно и в блоки причин не попадают.

Отмена относится к месяцу по дате отмены (cancelledDate).
В таблицах рядом с процентом стоит количество: «40% · 23».
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
from lib.cancel_reasons import classify, percent
from last_comment import get_last_comment_plain

app = Flask(__name__)

# Блоки отчёта: ключ → (заголовок, статус CRM, подпись источника)
KINDS = [
    ('client', 'Отмены клиентом', 'CANCELED_BY_CLIENT', 'наши причины'),
    ('bank', 'Отмены банком', 'CANCELED_BY_BANK', 'причины банка'),
]
REFUND_STATUS = 'REFUND'
CANCELED_STATUSES = tuple(status for _k, _t, status, _s in KINDS) + (REFUND_STATUS,)

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
def load_requests(start_dt, end_dt):
    """Заявки, пришедшие за период: менеджер, месяц создания, статус, данные.

    Считаем по когорте создания: заявка относится к тому месяцу, когда пришла,
    поэтому знаменатель — все пришедшие заявки менеджера, а не только отмены.
    """
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT data->>'callCenterManagerId',
               to_char(to_timestamp((data->>'created')::bigint / 1000), 'YYYY-MM'),
               data->>'status',
               data
        FROM requests
        WHERE (data->>'created') ~ '^[0-9]+$'
          AND to_timestamp((data->>'created')::bigint / 1000) BETWEEN %s AND %s
        """,
        (start_dt, end_dt),
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
    """Причины отмен в процентах от всех пришедших заявок менеджера."""
    months = month_keys(start_dt, end_dt)
    received = defaultdict(Counter)                                  # менеджер → месяц → заявок
    reasons = defaultdict(lambda: defaultdict(lambda: defaultdict(Counter)))
    # reasons[менеджер][тип][месяц][причина] = шт.
    types = defaultdict(Counter)                                     # менеджер → статус → шт.

    for manager, month, status, data in load_requests(start_dt, end_dt):
        if month not in months:
            continue
        name = manager_name(manager)
        received[name][month] += 1
        if status not in CANCELED_STATUSES:
            continue
        types[name][status] += 1
        kind = next((k for k, _t, s, _l in KINDS if s == status), None)
        if kind is None:
            continue   # возвраты в блоки причин не идут
        reasons[name][kind][month][classify(get_last_comment_plain(data) or '')] += 1

    result = []
    for name in received:
        blocks = []
        for kind, title, _status, source in KINDS:
            by_month = {
                month: sum(reasons[name][kind][month].values()) for month in months
            }
            rows = []
            for reason in sorted(
                {reason for month in months for reason in reasons[name][kind][month]},
                key=lambda r: -sum(reasons[name][kind][m][r] for m in months),
            ):
                rows.append({
                    'reason': reason,
                    'count': {m: reasons[name][kind][m][reason] for m in months},
                    # процент от всех пришедших заявок месяца
                    'pct': {
                        m: percent(reasons[name][kind][m][reason], received[name][m])
                        for m in months
                    },
                })
            blocks.append({
                'kind': kind,
                'title': title,
                'source': source,
                'by_month': by_month,
                'total': sum(by_month.values()),
                'rows': rows,
            })
        total_canceled = sum(types[name].values())
        result.append({
            'name': name,
            'blocks': blocks,
            'types': types[name],
            'received': {m: received[name][m] for m in months},
            'received_total': sum(received[name][m] for m in months),
            'total': total_canceled,
            'pct_total': percent(total_canceled, sum(received[name][m] for m in months)),
        })
    result.sort(key=lambda item: -item['received_total'])
    return result, months


# --------------------------------------------------------------------------
# Оформление
# --------------------------------------------------------------------------
def format_iso(value):
    return value.strftime('%Y-%m-%d')


def cell_html(row, month):
    """Ячейка: процент и количество — «40% · 23»."""
    count = row['count'][month]
    if not count:
        return '<td class="muted">—</td>'
    return (
        f'<td><span class="pct">{row["pct"][month]:.0f}%</span>'
        f'<span class="qty">· {count}</span></td>'
    )


def render_html(report, months, start_dt, end_dt):
    same_year = len({m.split('-')[0] for m in months}) == 1
    month_headers = ''.join(
        f'<th>{html.escape(month_label(m, same_year))}</th>' for m in months
    )

    summary_rows = []
    for item in report:
        cells = []
        for m in months:
            got = item['received'][m]
            canceled = sum(block['by_month'][m] for block in item['blocks'])
            if not got:
                cells.append('<td class="muted">—</td>')
                continue
            cells.append(
                f'<td><span class="apps">{got}</span>'
                f'<span class="sub2">{canceled} отм. · {percent(canceled, got):.0f}%</span></td>'
            )
        summary_rows.append(
            f'<tr><td>{html.escape(item["name"])}</td>{"" .join(cells)}'
            f'<td class="total">{item["received_total"]}</td>'
            f'<td>{item["types"].get("CANCELED_BY_CLIENT") or "—"}</td>'
            f'<td>{item["types"].get("CANCELED_BY_BANK") or "—"}</td>'
            f'<td class="total">{item["total"]} · {item["pct_total"]:.0f}%</td></tr>'
        )

    cards = []
    for item in report:
        blocks_html = []
        for block in item['blocks']:
            if not block['rows']:
                blocks_html.append(
                    f'<div class="block"><h3>{html.escape(block["title"])}'
                    f'<small>{html.escape(block["source"])}</small></h3>'
                    f'<p class="empty">Отмен этого типа не было</p></div>'
                )
                continue
            body = ''.join(
                f'<tr><td>{html.escape(row["reason"])}</td>'
                + ''.join(cell_html(row, m) for m in months)
                + '</tr>'
                for row in block['rows']
            )
            footer = ''.join(
                f'<td>{block["by_month"][m] or 0}</td>' for m in months
            )
            share = ''.join(
                f'<td>{percent(block["by_month"][m], item["received"][m]):.0f}%</td>'
                for m in months
            )
            blocks_html.append(f'''
            <div class="block">
                <h3>{html.escape(block['title'])}
                    <small>{html.escape(block['source'])} · {block['total']} из {item['received_total']} заявок
                        ({percent(block['total'], item['received_total']):.0f}%)</small></h3>
                <table class="reasons">
                    <thead><tr><th>Причина</th>{month_headers}</tr></thead>
                    <tbody>{body}
                        <tr class="footer"><td>Всего отмен, шт.</td>{footer}</tr>
                        <tr class="footer share"><td>Доля от всех заявок месяца</td>{share}</tr>
                    </tbody>
                </table>
            </div>''')

        cards.append(f'''
        <section class="card manager">
            <div class="card-head">
                <h2>{html.escape(item['name'])}</h2>
                <span class="count">Заявок за период: {item['received_total']} ·
                    отмен {item['total']} ({item['pct_total']:.0f}%)</span>
            </div>
            {''.join(blocks_html)}
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
    h3 {{ margin:18px 0 8px; font-size:14px; color:#2a3a36; display:flex; gap:8px;
        align-items:baseline; flex-wrap:wrap; }}
    h3 small {{ font-weight:400; color:var(--muted); font-size:12px; }}
    .count {{ font-size:12px; color:var(--muted); }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    thead th {{ text-align:left; padding:10px 12px; color:#6b7a76; font-weight:600;
        border-bottom:1px solid var(--line); background:#f7f9f8; }}
    tbody td {{ padding:10px 12px; border-bottom:1px solid #f0f3f2; }}
    tbody td:not(:first-child), thead th:not(:first-child) {{ width:17%; }}
    tr.footer td {{ font-weight:700; color:#3a4a46; border-bottom:none; }}
    .pct {{ font-weight:600; }}
    .apps {{ font-weight:600; }}
    .sub2 {{ display:block; color:var(--muted); font-size:11px; margin-top:2px; }}
    tr.share td {{ font-weight:600; color:#4a5a56; background:#fbfcfc; }}
    .qty {{ color:var(--muted); font-size:12px; margin-left:4px; }}
    td.muted {{ color:#c3ccc9; }}
    .summary table tbody td:first-child {{ font-weight:600; }}
    .empty {{ color:var(--muted); font-size:13px; margin:6px 0 0; }}
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
            <span class="count">Всего за период: {sum(i['total'] for i in report)}</span>
        </div>
        <table>
            <thead><tr><th>Менеджер</th>{month_headers}<th>Заявок</th>
                <th>Отмен клиентом</th><th>Отмен банком</th><th>Отмен всего</th></tr></thead>
            <tbody>{''.join(summary_rows)}</tbody>
        </table>
    </section>
    {''.join(cards)}
    <p class="note">Все проценты считаются от общего числа пришедших заявок менеджера
        в этом месяце, а не от числа отмен. Заявка относится к месяцу, когда пришла;
        причина — та, что зафиксирована при отмене (даже если отменили позже).
        В ячейке: доля от пришедших заявок и количество штук через «·».
        В сводке под числом заявок — сколько из них отменено и какой это процент.
        Отмены клиентом и отмены банком — разные процессы, поэтому отдельные таблицы;
        «Отмены банком» заметно чаще остаются без причины. Возвраты в таблицы причин
        не входят, но учтены в колонке «Отмен всего».</p>
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
    """Excel: лист на каждый блок (клиент / банк), проценты от всех заявок."""
    wb = Workbook()
    wb.remove(wb.active)
    same_year = len({m.split('-')[0] for m in months}) == 1
    month_names = [month_label(m, same_year) for m in months]

    header = (
        ['Менеджер', 'Причина']
        + [f'{m}, % от заявок' for m in month_names]
        + [f'{m}, шт.' for m in month_names]
        + ['Всего, шт.', '% от всех заявок менеджера']
    )
    grey = PatternFill('solid', fgColor='F2F5F4')

    for kind, title, _status, _source in KINDS:
        ws = wb.create_sheet(title)
        ws.append(header)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(wrap_text=True, vertical='center')

        for item in report:
            block = next(b for b in item['blocks'] if b['kind'] == kind)
            if not block['rows']:
                continue
            for row in block['rows']:
                ws.append(
                    [item['name'], row['reason']]
                    + [round(row['pct'][m] / 100, 4) for m in months]
                    + [row['count'][m] for m in months]
                    + [sum(row['count'].values()),
                       round(percent(sum(row['count'].values()), item['received_total']) / 100, 4)]
                )
            ws.append(
                [item['name'], 'Всего отмен, шт.']
                + [round(percent(block['by_month'][m], item['received'][m]) / 100, 4) for m in months]
                + [block['by_month'][m] for m in months]
                + [block['total'],
                   round(percent(block['total'], item['received_total']) / 100, 4)]
            )
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)
                cell.fill = grey

        # в строке «Всего» процент остаётся процентом, штуки — числом
        for row_index in range(2, ws.max_row + 1):
            is_total = str(ws.cell(row_index, 2).value or '').startswith('Всего отмен')
            for col in range(3, 3 + len(months)):
                ws.cell(row_index, col).number_format = '0.0%'
            for col in range(3 + len(months), 3 + 2 * len(months)):
                ws.cell(row_index, col).number_format = '0'
            last = 3 + 2 * len(months)
            ws.cell(row_index, last).number_format = '0'
            ws.cell(row_index, last + 1).number_format = '0.0%'
            if is_total:
                pass

        widths = [16, 32] + [13] * len(months) + [10] * len(months) + [11, 15]
        for index, width in enumerate(widths, start=1):
            ws.column_dimensions[chr(64 + index)].width = width
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
        report, months = build_report(start_dt, end_dt)
    except Exception as exc:
        return Response(
            f'<h1>Ошибка расчёта отмен</h1><pre>{html.escape(str(exc))}</pre>',
            status=500,
            mimetype='text/html; charset=utf-8',
        ), 500
    return Response(
        render_html(report, months, start_dt, end_dt),
        mimetype='text/html; charset=utf-8',
    )


@app.route('/managers/cancel/export')
def managers_cancel_export():
    """Excel с причинами отмен по менеджерам (листы «клиент» и «банк»)."""
    start_dt, end_dt = parse_period()
    report, months = build_report(start_dt, end_dt)
    output = build_xlsx(report, months, start_dt, end_dt)
    filename = f'otmeny_{format_iso(start_dt)}_{format_iso(end_dt)}.xlsx'
    return Response(
        output.read(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5004)