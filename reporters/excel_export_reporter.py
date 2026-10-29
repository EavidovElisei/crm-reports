#!/usr/bin/env python3
"""
Репортер для экспорта данных в Excel

Отдельный отчёт `/export/excel`: сводка, статусы, менеджеры, динамика по дням
и детальная таблица заявок. Экспорт из HTML-отчёта (/report) делает crm_reporter.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, send_file, jsonify
import pandas as pd
from datetime import datetime
import io
import logging

from config import MANAGER_NAMES, STATUS_LABELS
from last_comment import get_last_comment_plain
from reporters.crm_reporter import analyze_data, get_requests_from_db

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - EXCEL_EXPORT - %(message)s')

app = Flask(__name__)

# Начало данных в БД для экспорта «по умолчанию»
DEFAULT_START = datetime(2025, 4, 1)


def get_invoice_amount(order):
    """Сумма выставленных счетов из enrichment"""
    if not order or not isinstance(order, dict):
        return None
    enrichment = order.get('enrichment', {})
    if not enrichment or not isinstance(enrichment, dict):
        return None
    total = 0
    for key in ('main_order', 'additional_order'):
        block = enrichment.get(key)
        if block and isinstance(block, dict):
            total += block.get('amount', 0) or 0
    return total if total > 0 else None


def get_tid(order):
    """TID (Transaction ID): request.deviceInfo[0].tid, затем корневой deviceInfo[0].tid"""
    if not order or not isinstance(order, dict):
        return "—"

    candidates = []
    req = order.get('request')
    if isinstance(req, dict) and isinstance(req.get('deviceInfo'), list):
        candidates.append(req['deviceInfo'])
    if isinstance(order.get('deviceInfo'), list):
        candidates.append(order['deviceInfo'])

    for device_info in candidates:
        if not device_info:
            continue
        first_device = device_info[0]
        if isinstance(first_device, dict):
            tid = first_device.get('tid')
            if tid is not None and tid != '':
                return str(tid).strip()

    return "—"


def create_excel_report(data, start_date, end_date):
    """Создание Excel отчета с теми же данными, что и в HTML отчете"""
    # Создаем Excel файл в памяти
    output = io.BytesIO()
    valid_rows = [order for order in (data or []) if order and isinstance(order, dict)]

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Лист 1: Сводка
        total_orders = len(valid_rows)
        paid_requests = len([o for o in valid_rows if o.get('status') in ['INCOME_PAID', 'KKT_LINKED', 'COMPLETED']])
        total_amount = sum(get_invoice_amount(o) or 0 for o in valid_rows)

        summary_data = [
            ['Показатель', 'Значение'],
            ['Период', f'{start_date.strftime("%d.%m.%Y")} - {end_date.strftime("%d.%m.%Y")}'],
            ['Всего заявок', total_orders],
            ['Оплаченные заявки', paid_requests],
            ['Новые заявки', len([o for o in valid_rows if o.get('status') == 'NEW'])],
            ['Завершенные заявки', len([o for o in valid_rows if o.get('status') == 'COMPLETED'])],
            ['Отмененные заявки', len([o for o in valid_rows if o.get('status') == 'CANCELED_BY_CLIENT'])],
            ['Общая сумма', f"{total_amount:,}".replace(',', ' ') + " ₽"],
            ['', ''],
            ['Дата создания отчета', datetime.now().strftime('%d.%m.%Y %H:%M:%S')]
        ]

        summary_df = pd.DataFrame(summary_data[1:], columns=summary_data[0])
        summary_df.to_excel(writer, sheet_name='Сводка', index=False)

        # Лист 2: Статистика по статусам
        status_counts = {}
        for order in valid_rows:
            status = order.get('status', 'UNKNOWN')
            status_counts[status] = status_counts.get(status, 0) + 1

        status_data = []
        for status, count in status_counts.items():
            status_data.append([STATUS_LABELS.get(status, status), count])

        status_df = pd.DataFrame(status_data, columns=['Статус', 'Количество'])
        status_df.to_excel(writer, sheet_name='По статусам', index=False)

        # Лист 3: Статистика по менеджерам
        manager_counts = {}
        for order in valid_rows:
            manager_id = order.get('callCenterManagerId', 'unknown')
            manager_counts[manager_id] = manager_counts.get(manager_id, 0) + 1

        manager_data = []
        for manager_id, count in manager_counts.items():
            manager_name = MANAGER_NAMES.get(manager_id, f'Менеджер {manager_id}')
            manager_data.append([manager_name, count])

        manager_df = pd.DataFrame(manager_data, columns=['Менеджер', 'Количество заявок'])
        manager_df.to_excel(writer, sheet_name='По менеджерам', index=False)

        # Лист 4: Динамика по дням
        daily_counts = {}
        for order in valid_rows:
            created_timestamp = order.get('created')
            if not created_timestamp:
                continue
            try:
                date_str = datetime.fromtimestamp(created_timestamp / 1000).strftime('%Y-%m-%d')
            except (ValueError, TypeError, OSError):
                continue
            daily_counts[date_str] = daily_counts.get(date_str, 0) + 1

        daily_data = []
        for date_str, count in sorted(daily_counts.items()):
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            daily_data.append([date_obj.strftime('%d.%m.%Y'), count])

        daily_df = pd.DataFrame(daily_data, columns=['Дата', 'Количество заявок'])
        daily_df.to_excel(writer, sheet_name='Динамика по дням', index=False)

        # Лист 5: Детальные данные заявок (та же таблица, что и в отчете)
        detailed_data = []
        for index, order in enumerate(valid_rows, 1):
            try:
                created_timestamp = order.get('created', 0)
                created_date = datetime.fromtimestamp(created_timestamp / 1000) if created_timestamp else datetime.now()
            except (ValueError, TypeError, OSError):
                created_date = datetime.now()

            status = order.get('status', 'UNKNOWN')
            manager_id = order.get('callCenterManagerId', 'unknown')
            invoice_amount = get_invoice_amount(order)
            amount_text = f"{invoice_amount:,}".replace(',', ' ') + " ₽" if invoice_amount else "—"

            detailed_data.append([
                index,
                order.get('organizationName', 'Не указано'),
                order.get('organizationInn', 'Не указан'),
                get_tid(order),
                amount_text,
                STATUS_LABELS.get(status, status),
                get_last_comment_plain(order) or "—",
                MANAGER_NAMES.get(manager_id, f"Менеджер {manager_id}"),
                created_date.strftime('%d.%m.%Y %H:%M')
            ])

        detailed_df = pd.DataFrame(detailed_data, columns=[
            '№', 'Организация', 'ИНН', 'TID', 'Сумма', 'Статус',
            'Последний комментарий', 'Менеджер', 'Дата создания'
        ])
        detailed_df.to_excel(writer, sheet_name='Детальные данные', index=False)

    output.seek(0)
    return output


@app.route('/export/excel')
def export_excel():
    """API для экспорта данных в Excel"""
    try:
        # Получаем параметры периода
        start_param = request.args.get('start')
        end_param = request.args.get('end')

        if start_param and end_param:
            start_date = datetime.strptime(start_param, '%Y-%m-%d')
            end_date = datetime.strptime(end_param, '%Y-%m-%d')

            # Устанавливаем время для поиска в БД
            search_start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            search_end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            # По умолчанию - все данные (как в отчете)
            now = datetime.now()
            start_date = DEFAULT_START
            end_date = now
            search_start = DEFAULT_START
            search_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        # Получаем данные из БД (используем ту же функцию, что и в отчете)
        data = get_requests_from_db(search_start, search_end)

        # Создаем Excel отчет
        excel_file = create_excel_report(data, start_date, end_date)

        # Формируем имя файла
        filename = f'crm_report_{start_date.strftime("%Y%m%d")}_{end_date.strftime("%Y%m%d")}.xlsx'

        return send_file(
            excel_file,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        logging.error(f"Ошибка экспорта в Excel: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/excel')
def export_excel_legacy():
    """Поддержка старого маршрута /excel для экспорта"""
    return export_excel()


@app.route('/export/status')
def export_status():
    """API для проверки статуса экспорта"""
    return jsonify({
        'status': 'ready',
        'message': 'Экспорт в Excel доступен',
        'endpoints': {
            'excel_export': '/export/excel?start=YYYY-MM-DD&end=YYYY-MM-DD',
            'excel_export_default': '/export/excel'
        }
    })


if __name__ == '__main__':
    app.run(debug=True)