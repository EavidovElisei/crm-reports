#!/usr/bin/env python3
"""
Репортер - генерирует HTML отчеты из данных БД
"""
import psycopg2
import json
from datetime import datetime, timedelta
from flask import Flask, request, Response
from config import DB_CONFIG, MANAGER_NAMES, STATUS_LABELS, STATUS_CLASSES, CRM_CONFIG

app = Flask(__name__)


def get_requests_from_db(start_date, end_date):
    """Получение заявок из БД за период"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Используем >= для start_date и <= для end_date для включения всего периода
    cur.execute("""
        SELECT data FROM requests 
        WHERE created_at >= %s AND created_at <= %s
        ORDER BY created_at DESC
    """, (start_date, end_date))

    results = cur.fetchall()
    cur.close()
    conn.close()

    # Фильтруем None значения и проверяем что данные являются словарем
    valid_data = []
    for row in results:
        if row[0] is not None and isinstance(row[0], dict):
            valid_data.append(row[0])

    return valid_data


def get_invoice_amount(order):
    """Получение суммы выставленных счетов"""
    if not order or not isinstance(order, dict):
        return None

    enrichment = order.get('enrichment', {})
    if not enrichment or not isinstance(enrichment, dict):
        return None

    main_order = enrichment.get('main_order', {})
    additional_order = enrichment.get('additional_order', {})

    # Безопасное получение сумм с проверкой на None
    main_amount = 0
    if isinstance(main_order, dict):
        main_amount_value = main_order.get('amount')
        if main_amount_value is not None and isinstance(main_amount_value, (int, float)):
            main_amount = main_amount_value

    additional_amount = 0
    if isinstance(additional_order, dict):
        additional_amount_value = additional_order.get('amount')
        if additional_amount_value is not None and isinstance(additional_amount_value, (int, float)):
            additional_amount = additional_amount_value

    total_amount = main_amount + additional_amount
    return int(total_amount) if total_amount > 0 else None


def get_registration_status(order):
    """Получение статуса регистрации"""
    if not order or not isinstance(order, dict):
        return 'не зарегистрирован'

    enrichment = order.get('enrichment', {})
    if not enrichment or not isinstance(enrichment, dict):
        return 'не зарегистрирован'

    fns_status = enrichment.get('fns_registration_status')
    appointment_date = enrichment.get('registration_appointment_date')

    if fns_status == 'registered':
        return 'зарегистрирован'
    elif appointment_date:
        try:
            # Конвертируем timestamp в дату
            date_obj = datetime.fromtimestamp(appointment_date / 1000)
            return f'записан на {date_obj.strftime("%d.%m.%Y")}'
        except (ValueError, TypeError, OSError):
            return 'не зарегистрирован'
    else:
        return 'не зарегистрирован'


def analyze_data(data):
    """Анализ данных"""
    if not data:
        return {}

    # Фильтруем валидные записи
    valid_data = [order for order in data if order and isinstance(order, dict)]

    total_orders = len(valid_data)
    paid_and_processed_orders = len([
        order for order in valid_data
        if order.get('status') in ['INCOME_PAID', 'INCOME_PARTIALLY_PAID', 'KKT_LINKED', 'COMPLETED']
    ])
    invoiced_orders = len([order for order in valid_data
                           if order.get('status') == 'INCOME_CREATED'])
    conversion_rate = (paid_and_processed_orders / total_orders * 100) if total_orders > 0 else 0

    # Статистика по статусам
    status_order = [
        'DRAFT',
        'NEW',
        'INCOME_CREATED',
        'INCOME_PAID',
        'INCOME_PARTIALLY_PAID',
        'KKT_LINKED',
        'COMPLETED',
        'REFUND',
        'CANCELED_BY_CLIENT'
    ]
    status_stats = {}
    for status in status_order:
        status_stats[status] = len([order for order in valid_data if order.get('status') == status])

    # Статистика по менеджерам
    manager_stats = {}
    for order in valid_data:
        manager_id = order.get('callCenterManagerId')
        if manager_id:
            manager_stats[manager_id] = manager_stats.get(manager_id, 0) + 1

    # Динамика по дням
    daily_stats = {}
    for order in valid_data:
        created_timestamp = order.get('created')
        if created_timestamp:
            try:
                date = datetime.fromtimestamp(created_timestamp / 1000).strftime('%Y-%m-%d')
                daily_stats[date] = daily_stats.get(date, 0) + 1
            except (ValueError, TypeError, OSError):
                continue

    return {
        'total_orders': total_orders,
        'paid_orders': paid_and_processed_orders,
        'invoiced_orders': invoiced_orders,
        'conversion_rate': round(conversion_rate, 1),
        'status_stats': status_stats,
        'manager_stats': manager_stats,
        'daily_stats': daily_stats,
        'raw_data': valid_data
    }


def truncate_text(text, max_length):
    """Обрезка текста"""
    return text[:max_length] + '...' if len(text) > max_length else text


def generate_html_report(analytics, start_date, end_date):
    """Генерация HTML отчета с современными стилями"""
    # Подготовка данных для графиков
    status_chart_data = []
    for status in [
        'DRAFT',
        'NEW',
        'INCOME_CREATED',
        'INCOME_PAID',
        'INCOME_PARTIALLY_PAID',
        'KKT_LINKED',
        'COMPLETED',
        'REFUND',
        'CANCELED_BY_CLIENT'
    ]:
        if status in analytics['status_stats'] and analytics['status_stats'][status] > 0:
            status_chart_data.append({
                'label': STATUS_LABELS.get(status, status),
                'value': analytics['status_stats'][status]
            })

    manager_chart_data = []
    for manager_id, count in analytics['manager_stats'].items():
        manager_chart_data.append({
            'label': MANAGER_NAMES.get(manager_id, f'Менеджер {manager_id}'),
            'value': count
        })

    # Подготовка данных для графика динамики
    daily_chart_data = []
    weekdays_ru = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    for date in sorted(analytics['daily_stats'].keys()):
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        weekday = weekdays_ru[date_obj.weekday()]
        daily_chart_data.append({
            'date': f"{date_obj.strftime('%d.%m')} ({weekday})",
            'value': analytics['daily_stats'][date]
        })

    # Генерация таблицы заявок
    table_rows = ""
    for index, order in enumerate(analytics['raw_data'], 1):
        # Проверяем что order не None и является словарем
        if not order or not isinstance(order, dict):
            continue

        # Безопасное получение данных с проверками
        try:
            created_timestamp = order.get('created', 0)
            created_date = datetime.fromtimestamp(created_timestamp / 1000) if created_timestamp else datetime.now()
        except (ValueError, TypeError, OSError):
            created_date = datetime.now()

        status = order.get('status', 'UNKNOWN')
        status_class = STATUS_CLASSES.get(status, '')
        status_text = STATUS_LABELS.get(status, status)

        manager_id = order.get('callCenterManagerId', 'unknown')
        manager_name = MANAGER_NAMES.get(manager_id, f"Менеджер {manager_id}")

        order_id = order.get('id', 'unknown')
        org_name = order.get('organizationName', 'Не указано')
        org_inn = order.get('organizationInn', 'Не указан')

        # Обрезаем название организации
        org_name_short = truncate_text(org_name, 40)

        # Получаем сумму счетов
        invoice_amount = get_invoice_amount(order)
        amount_text = f"{invoice_amount:,}".replace(',', ' ') + " ₽" if invoice_amount else "—"

        # Получаем статус регистрации
        registration_status = get_registration_status(order)

        table_rows += f"""
                        <tr class="clickable-row" onclick="openRequest({order_id})" title="Кликните для открытия заявки">
                            <td>{index}</td>
                            <td title="{org_name}">{org_name_short}</td>
                            <td>{org_inn}</td>
                            <td>{amount_text}</td>
                            <td><span class="status-badge {status_class}">{status_text}</span></td>
                            <td>{registration_status}</td>
                            <td>{manager_name}</td>
                            <td>{created_date.strftime('%d.%m.%Y %H:%M')}</td>
                        </tr>
        """

    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CRM Отчет - {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 25px 50px rgba(0, 0, 0, 0.15);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            font-weight: 300;
        }}

        .period-selector {{
            background: #f8f9fa;
            padding: 20px;
            text-align: center;
            border-bottom: 1px solid #e9ecef;
        }}

        .period-form {{
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 15px;
            flex-wrap: wrap;
        }}

        .period-form input[type="date"] {{
            padding: 8px 12px;
            border: 2px solid #dee2e6;
            border-radius: 8px;
            font-size: 1rem;
        }}

        .period-form button {{
            background: #667eea;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: background 0.3s;
        }}

        .period-form button:hover {{
            background: #5a6fd8;
        }}

        .quick-periods {{
            margin-top: 10px;
            display: flex;
            justify-content: center;
            gap: 10px;
            flex-wrap: wrap;
        }}

        .quick-periods button {{
            background: #6c757d;
            color: white;
            padding: 5px 12px;
            border: none;
            border-radius: 15px;
            cursor: pointer;
            font-size: 0.9em;
            transition: background 0.3s;
        }}

        .quick-periods button:hover {{
            background: #5a6268;
        }}

        .period {{
            background: #f8f9fa;
            padding: 15px;
            text-align: center;
            font-size: 1.2em;
            color: #495057;
            border-bottom: 1px solid #e9ecef;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
        }}

        .stat-card {{
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
            text-align: center;
            transition: transform 0.3s;
        }}

        .stat-card:hover {{
            transform: translateY(-5px);
        }}

        .stat-value {{
            font-size: 2.5em;
            font-weight: bold;
            color: #2c3e50;
            margin-bottom: 5px;
        }}

        .stat-label {{
            color: #6c757d;
            font-size: 0.9em;
            font-weight: 500;
        }}

        .chart-section {{
            padding: 30px;
            background: #f8f9fa;
        }}

        .chart-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 30px;
            margin-bottom: 30px;
        }}

        .chart-container {{
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
        }}

        .chart-title {{
            font-size: 1.3em;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 20px;
            text-align: center;
        }}

        .table-section {{
            padding: 30px;
        }}

        .table-container {{
            background: white;
            border-radius: 15px;
            overflow: hidden;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
        }}

        .table-header {{
            background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
            color: white;
            padding: 20px;
            font-size: 1.2em;
            font-weight: 600;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
        }}

        th, td {{
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid #e9ecef;
        }}

        th {{
            background: #f8f9fa;
            font-weight: 600;
            color: #495057;
            font-size: 0.9em;
        }}

        tr:hover {{
            background: #f8f9fa;
            cursor: pointer;
        }}

        .clickable-row {{
            cursor: pointer;
            transition: background-color 0.2s;
        }}

        .clickable-row:hover {{
            background: #e9ecef !important;
        }}

        .status-badge {{
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: 600;
        }}

        .status-new {{ background: #e3f2fd; color: #1976d2; }}
        .status-draft {{ background: #f3e5f5; color: #7b1fa2; }}
        .status-income-created {{ background: #fff3e0; color: #f57c00; }}
        .status-income-paid {{ background: #e8f5e8; color: #388e3c; }}
        .status-income-partially-paid {{ background: #fff8e1; color: #f9a825; }}
        .status-kkt-linked {{ background: #e8f5e8; color: #2e7d32; }}
        .status-completed {{ background: #e8f5e8; color: #2e7d32; }}
        .status-refund {{ background: #e3f2fd; color: #1565c0; }}
        .status-canceled {{ background: #ffebee; color: #d32f2f; }}

        @media print {{
            .period-selector {{ display: none; }}
            body {{ background: white; }}
            .container {{ box-shadow: none; }}
        }}

        @media (max-width: 768px) {{
            .chart-grid {{ grid-template-columns: 1fr; }}
            .period-form {{ flex-direction: column; }}
        }}
    </style>
</head>
<body>

    <div class="container">
        <div class="header">
            <h1>📊 CRM Отчет</h1>
            <p>Управленческая отчетность и аналитика</p>
        </div>

        <div class="period-selector">
            <form class="period-form" method="GET">
                <label>с:</label>
                <input type="date" name="start" value="{start_date.strftime('%Y-%m-%d')}" required>
                <label>по:</label>
                <input type="date" name="end" value="{end_date.strftime('%Y-%m-%d')}" required>
                <button type="submit">📊 Сформировать отчет</button>
            </form>
            <div class="quick-periods">
                <button onclick="setQuickPeriod('today')">Сегодня</button>
                <button onclick="setQuickPeriod('yesterday')">Вчера</button>
                <button onclick="setQuickPeriod('week')">Неделя</button>
                <button onclick="setQuickPeriod('month')">Месяц</button>
                <button onclick="setQuickPeriod('quarter')">Квартал</button>
            </div>
        </div>

        <div class="period">
            <strong>Период:</strong> {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{analytics['total_orders']}</div>
                <div class="stat-label">Всего заявок</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{analytics['paid_orders']}</div>
                <div class="stat-label">Оплаченных</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{analytics['conversion_rate']}%</div>
                <div class="stat-label">Конверсия</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{analytics['invoiced_orders']}</div>
                <div class="stat-label">Ожидаем оплат</div>
            </div>
        </div>

        <div class="chart-section">
            <div class="chart-grid">
                <div class="chart-container">
                    <div class="chart-title">Распределение по статусам</div>
                    <canvas id="statusChart"></canvas>
                </div>
                <div class="chart-container">
                    <div class="chart-title">Заявки по менеджерам</div>
                    <canvas id="managerChart"></canvas>
                </div>
            </div>
            <div class="chart-container">
                <div class="chart-title">Динамика создания заявок</div>
                <canvas id="timelineChart"></canvas>
            </div>
        </div>

        <div class="table-section">
            <div class="table-container">
                <div class="table-header">Детальная информация по заявкам</div>
                <table>
                    <thead>
                        <tr>
                            <th>№</th>
                            <th>Организация</th>
                            <th>ИНН</th>
                            <th>Сумма счетов</th>
                            <th>Статус</th>
                            <th>Регистрация</th>
                            <th>Менеджер</th>
                            <th>Дата создания</th>
                        </tr>
                    </thead>
                    <tbody>
                        {table_rows}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        // Функция для открытия заявки в новом окне
        function openRequest(orderId) {{
            if (orderId && orderId !== 'unknown') {{
                const url = `{CRM_CONFIG['admin_url']}?id=${{orderId}}`;
                window.open(url, '_blank');
            }} else {{
                alert('ID заявки не найден');
            }}
        }}

        // Функция для быстрого выбора периода
        function setQuickPeriod(period) {{
            const now = new Date();
            let startDate, endDate;

            switch(period) {{
                case 'today':
                    startDate = endDate = now;
                    break;
                case 'yesterday':
                    const yesterday = new Date(now);
                    yesterday.setDate(yesterday.getDate() - 1);
                    startDate = endDate = yesterday;
                    break;
                case 'week':
                    endDate = now;
                    startDate = new Date(now);
                    startDate.setDate(startDate.getDate() - 7);
                    break;
                case 'month':
                    endDate = now;
                    startDate = new Date(now);
                    startDate.setMonth(startDate.getMonth() - 1);
                    break;
                case 'quarter':
                    endDate = now;
                    startDate = new Date(now);
                    startDate.setMonth(startDate.getMonth() - 3);
                    break;
            }}

            document.querySelector('input[name="start"]').value = startDate.toISOString().split('T')[0];
            document.querySelector('input[name="end"]').value = endDate.toISOString().split('T')[0];
        }}

        // Данные для графиков
        const statusData = {json.dumps(status_chart_data)};
        const managerData = {json.dumps(manager_chart_data)};
        const dailyData = {json.dumps(daily_chart_data)};

        // График статусов
        const statusCtx = document.getElementById('statusChart').getContext('2d');
        new Chart(statusCtx, {{
            type: 'doughnut',
            data: {{
                labels: statusData.map(item => item.label),
                datasets: [{{
                    data: statusData.map(item => item.value),
                    backgroundColor: [
                        '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40'
                    ]
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ position: 'bottom' }}
                }}
            }}
        }});

        // График менеджеров
        const managerCtx = document.getElementById('managerChart').getContext('2d');
        new Chart(managerCtx, {{
            type: 'bar',
            data: {{
                labels: managerData.map(item => item.label),
                datasets: [{{
                    label: 'Количество заявок',
                    data: managerData.map(item => item.value),
                    backgroundColor: '#667eea'
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{ y: {{ beginAtZero: true }} }}
            }}
        }});

        // График динамики
        const timelineCtx = document.getElementById('timelineChart').getContext('2d');
        new Chart(timelineCtx, {{
            type: 'line',
            data: {{
                labels: dailyData.map(item => item.date),
                datasets: [{{
                    label: 'Количество заявок',
                    data: dailyData.map(item => item.value),
                    borderColor: '#667eea',
                    backgroundColor: 'rgba(102, 126, 234, 0.1)',
                    fill: true,
                    tension: 0.4
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{ y: {{ beginAtZero: true }} }}
            }}
        }});
    </script>
</body>
</html>
    """
    return html


@app.route('/report')
def generate_report():
    """API для генерации отчета"""
    # Получаем параметры периода
    start_param = request.args.get('start')
    end_param = request.args.get('end')

    if start_param and end_param:
        # Парсим даты
        start_date = datetime.strptime(start_param, '%Y-%m-%d')
        end_date = datetime.strptime(end_param, '%Y-%m-%d')

        # Сохраняем даты для отображения (без времени)
        display_start = start_date
        display_end = end_date

        # Устанавливаем время для поиска в БД: начало дня для start_date, конец дня для end_date
        search_start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        search_end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        # По умолчанию - текущий месяц
        now = datetime.now()
        display_start = now.replace(day=1)
        display_end = now
        search_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        search_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    try:
        # Получаем данные из БД используя временной интервал
        data = get_requests_from_db(search_start, search_end)

        # Анализируем
        analytics = analyze_data(data)

        # Генерируем HTML (передаем даты для отображения)
        html = generate_html_report(analytics, display_start, display_end)

        return Response(html, mimetype='text/html')

    except Exception as e:
        return f"Ошибка генерации отчета: {str(e)}", 500


if __name__ == "__main__":
    from config import WEB_CONFIG

    app.run(**WEB_CONFIG)