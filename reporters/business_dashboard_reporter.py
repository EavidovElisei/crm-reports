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
    'client_id': 'cashdesk-rest-client',
    'client_secret': 'cashdesk-rest-client',
    'grant_type': 'password',
    'auth_url': 'https://kassa.stage.bifit.com/admin-api/oauth/token',
    'analytics_url': 'https://kassa.stage.bifit.com/admin-api/protected/boxed_kkm/analytics'
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
        
        # Если даты не указаны, используем последнюю неделю
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
        
        # Ищем последние данные аналитики
        cur.execute("""
            SELECT data FROM analytics_data 
            ORDER BY created_at DESC 
            LIMIT 1
        """)
        
        result = cur.fetchone()
        cur.close()
        conn.close()
        
        if result and result[0]:
            return result[0] if isinstance(result[0], dict) else json.loads(result[0])
        
        return {}
        
    except Exception as e:
        print(f"❌ Ошибка получения кешированных данных: {e}")
        return {}


def format_number(value):
    """Форматирование числа для отображения"""
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        if value == int(value):
            return f"{int(value):,}".replace(",", " ")
        else:
            return f"{value:.2f}".replace(".", ",")
    return str(value)


def get_status_class(value, thresholds):
    """Определение CSS класса на основе пороговых значений"""
    if value is None:
        return "status-unknown"
    
    if value >= thresholds.get('good', 0):
        return "status-good"
    elif value >= thresholds.get('warning', 0):
        return "status-warning"
    else:
        return "status-critical"


def generate_html_template(data):
    """Генерация HTML шаблона с данными"""
    alerts_html = ""
    for alert in data['alerts']:
        alert_class = alert['type']
        alerts_html += f'<div class="alert {alert_class}"><strong>{"Критично" if alert_class == "critical" else "Внимание"}:</strong> {alert["message"]}</div>'
    
    if not alerts_html:
        alerts_html = '<div class="alert info"><strong>Информация:</strong> Все системы работают нормально</div>'
    
    sim_remains_html = ""
    for provider, count in data['sim_remains'].items():
        status_class = "good" if count > 10 else ("warning" if count > 5 else "bad")
        sim_remains_html += f'<div class="metric"><span class="metric-label">{provider}</span><span class="metric-value {status_class}">{count}</span></div>'
    
    fn_ready_html = ""
    for location, count in data['fn15_ready'].items():
        fn_ready_html += f'<div class="metric"><span class="metric-label">{location}</span><span class="metric-value good">{format_number(count)}</span></div>'
    
    for location, count in data['fn36_ready'].items():
        fn_ready_html += f'<div class="metric"><span class="metric-label">{location} (ФН36)</span><span class="metric-value good">{format_number(count)}</span></div>'
    
    return f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Дашборд контроля бизнес-процессов</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f5f7fa;
            color: #2c3e50;
            line-height: 1.6;
        }}

        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            text-align: center;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}

        .header h1 {{
            font-size: 2rem;
            margin-bottom: 10px;
        }}

        .last-update {{
            opacity: 0.9;
            font-size: 0.9rem;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }}

        .alerts {{
            background: #fff;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}

        .alert {{
            padding: 12px 16px;
            border-radius: 6px;
            margin-bottom: 10px;
            border-left: 4px solid;
        }}

        .alert.critical {{
            background: #fdf2f2;
            border-color: #e74c3c;
            color: #c0392b;
        }}

        .alert.warning {{
            background: #fef9e7;
            border-color: #f39c12;
            color: #d68910;
        }}

        .alert.info {{
            background: #eaf4fd;
            border-color: #3498db;
            color: #2980b9;
        }}

        .dashboard-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .card {{
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            border-left: 4px solid;
        }}

        .card.success {{ border-color: #27ae60; }}
        .card.warning {{ border-color: #f39c12; }}
        .card.danger {{ border-color: #e74c3c; }}

        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }}

        .card-title {{
            font-size: 1.2rem;
            font-weight: 600;
            color: #2c3e50;
        }}

        .status-indicator {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-left: auto;
        }}

        .status-green {{ background: #27ae60; }}
        .status-yellow {{ background: #f39c12; }}
        .status-red {{ background: #e74c3c; }}

        .metric {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 0;
            border-bottom: 1px solid #ecf0f1;
        }}

        .metric:last-child {{
            border-bottom: none;
        }}

        .metric-label {{
            color: #7f8c8d;
            font-size: 0.9rem;
        }}

        .metric-value {{
            font-weight: 600;
            font-size: 1.1rem;
        }}

        .metric-value.good {{ color: #27ae60; }}
        .metric-value.warning {{ color: #f39c12; }}
        .metric-value.bad {{ color: #e74c3c; }}

        .progress-bar {{
            width: 100%;
            height: 8px;
            background: #ecf0f1;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 5px;
        }}

        .progress-fill {{
            height: 100%;
            background: linear-gradient(90deg, #27ae60, #2ecc71);
            transition: width 0.3s ease;
        }}

        .progress-fill.warning {{ background: linear-gradient(90deg, #f39c12, #e67e22); }}
        .progress-fill.danger {{ background: linear-gradient(90deg, #e74c3c, #c0392b); }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Контроль бизнес-процессов</h1>
        <div class="last-update">Последнее обновление: {data['current_time']}</div>
    </div>

    <div class="container">
        <!-- Критические алерты -->
        <div class="alerts">
            <h2 style="margin-bottom: 15px;">🚨 Критические уведомления</h2>
            {alerts_html}
        </div>

        <!-- Основные показатели -->
        <div class="dashboard-grid">
            <!-- Основные метрики -->
            <div class="card success">
                <div class="card-header">
                    <div class="card-title">Основные метрики</div>
                    <div class="status-indicator status-green"></div>
                </div>
                <div class="metric">
                    <span class="metric-label">Заявок за период</span>
                    <span class="metric-value good">{data['metrics']['requests_per_period']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Заявок за неделю</span>
                    <span class="metric-value good">{data['metrics']['requests_per_week']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Конверсия</span>
                    <span class="metric-value {data['status_classes']['conversion']}">{data['metrics']['conversion']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Среднее время оплаты</span>
                    <span class="metric-value good">{data['metrics']['avg_payment_time']}</span>
                </div>
            </div>

            <!-- Производственные показатели -->
            <div class="card warning">
                <div class="card-header">
                    <div class="card-title">Производственные показатели</div>
                    <div class="status-indicator status-yellow"></div>
                </div>
                <div class="metric">
                    <span class="metric-label">Время регистрации</span>
                    <span class="metric-value {data['status_classes']['avg_registration_time']}">{data['metrics']['avg_registration_time']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Время доставки</span>
                    <span class="metric-value good">{data['metrics']['avg_delivery_time']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Очередь регистрации</span>
                    <span class="metric-value {data['status_classes']['registration_queue']}">{data['metrics']['registration_queue']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Остаток ФН</span>
                    <span class="metric-value {data['status_classes']['fn_remains']}">{data['metrics']['fn_remains']}</span>
                </div>
            </div>

            <!-- Складские остатки -->
            <div class="card success">
                <div class="card-header">
                    <div class="card-title">Складские остатки</div>
                    <div class="status-indicator status-green"></div>
                </div>
                <div class="metric">
                    <span class="metric-label">Остаток ККМ 405</span>
                    <span class="metric-value good">{data['metrics']['remains_kkm405']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Остаток ОФД 405</span>
                    <span class="metric-value good">{data['metrics']['remains_ofd405']}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Потребление ФН/неделю</span>
                    <span class="metric-value good">{data['metrics']['fn_week_consumption']}</span>
                </div>
                {fn_ready_html}
            </div>

            <!-- SIM карты -->
            <div class="card success">
                <div class="card-header">
                    <div class="card-title">Остатки SIM карт</div>
                    <div class="status-indicator status-green"></div>
                </div>
                {sim_remains_html}
            </div>

            <!-- Автокомплекты -->
            <div class="card success">
                <div class="card-header">
                    <div class="card-title">Автокомплекты</div>
                    <div class="status-indicator status-green"></div>
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
    </div>
</body>
</html>'''


@app.route('/dashboard')
def business_dashboard():
    """Главная страница бизнес-дашборда"""
    
    # Получаем данные аналитики
    analytics = get_cached_analytics()
    
    # Если нет кешированных данных, пытаемся получить свежие
    if not analytics:
        analytics = get_analytics_data()
        if not analytics:
            analytics = {}
    
    # Извлекаем метрики
    requests_per_period = analytics.get('requestsPerPeriod', 0)
    requests_per_week = analytics.get('requestsPerWeek', 0)
    conversion = analytics.get('conversion', 0)
    avg_payment_time = analytics.get('averagePaymentTime', 0)
    avg_registration_time = analytics.get('averageRegistrationTime', 0)
    avg_delivery_time = analytics.get('averageDeliveryTime', 0)
    registration_queue = analytics.get('registrationQueue', 0)
    fn_remains = analytics.get('fnRemains', 0)
    fn_week_consumption = analytics.get('fnWeekConsumption', 0)
    remains_kkm405 = analytics.get('remainsKkm405', 0)
    remains_sim405 = analytics.get('remainsSim405', {})
    remains_ofd405 = analytics.get('remainsOfd405', 0)
    ready_fn15 = analytics.get('readyFn15', {})
    ready_fn36 = analytics.get('readyFn36', {})
    auto_kit_delivery_week = analytics.get('autoKitInDeliveryPerWeek', 0)
    auto_kit_delivery_month = analytics.get('autoKitInDeliveryPerMonth', 0)
    auto_kit_delivered_month = analytics.get('autoKitDeliveredPerMonth', 0)
    
    # Определяем алерты
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
    
    # Подготавливаем данные для шаблона
    template_data = {
        'current_time': datetime.now().strftime('%d.%m.%Y %H:%M'),
        'alerts': alerts,
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
        'status_classes': {
            'conversion': get_status_class(conversion, {'good': 0.8, 'warning': 0.7}),
            'registration_queue': get_status_class(registration_queue, {'good': 5, 'warning': 10}) if registration_queue <= 5 else ('status-warning' if registration_queue <= 10 else 'status-critical'),
            'fn_remains': get_status_class(fn_remains, {'good': 100, 'warning': 50}),
            'avg_registration_time': get_status_class(avg_registration_time, {'good': 10, 'warning': 15}) if avg_registration_time <= 10 else ('status-warning' if avg_registration_time <= 15 else 'status-critical')
        }
    }
    
    # Генерируем HTML контент
    html_content = generate_html_template(template_data)
    
    return Response(html_content, mimetype='text/html; charset=utf-8')


@app.route('/api/analytics')
def api_analytics():
    """API эндпоинт для получения данных аналитики"""
    min_date = request.args.get('min_date')
    max_date = request.args.get('max_date')
    
    analytics = get_analytics_data(min_date, max_date)
    
    if analytics:
        return Response(
            json.dumps(analytics, ensure_ascii=False, indent=2),
            mimetype='application/json; charset=utf-8'
        )
    else:
        return Response(
            json.dumps({'error': 'Не удалось получить данные аналитики'}, ensure_ascii=False),
            status=500,
            mimetype='application/json; charset=utf-8'
        )


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001) 