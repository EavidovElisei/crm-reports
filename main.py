#!/usr/bin/env python3
"""
Главное приложение - запускает планировщик и веб-сервер с репортерами
"""
import os
import sys

# Корень проекта в path — репортеры импортируют last_comment и др. из корня
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import logging
import threading
import importlib.util
import glob
from datetime import datetime
from flask import Flask, render_template_string
from config import WEB_CONFIG, SCHEDULER_CONFIG

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - MAIN - %(message)s')

# Создаем основное Flask приложение
app = Flask(__name__)


def find_reporters():
    """Поиск всех репортеров в папке"""
    pattern = './reporters/*_reporter.py'
    reporters = glob.glob(pattern)
    return sorted(reporters)


def load_reporter(reporter_path):
    """Загрузка репортера как модуля"""
    reporter_name = os.path.basename(reporter_path).replace('.py', '')

    try:
        spec = importlib.util.spec_from_file_location(reporter_name, reporter_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Получаем Flask приложение из модуля
        if hasattr(module, 'app'):
            return module.app, reporter_name
        else:
            logging.warning(f"⚠️ Репортер {reporter_name} не содержит объект 'app'")
            return None, reporter_name

    except Exception as e:
        logging.error(f"❌ Ошибка загрузки репортера {reporter_name}: {e}")
        return None, reporter_name


def register_reporters():
    """Регистрация всех репортеров в основном приложении"""
    reporters = find_reporters()
    registered_count = 0

    if not reporters:
        logging.warning("⚠️ Репортеры не найдены")
        return registered_count

    logging.info(f"📋 Найдено репортеров: {len(reporters)}")

    for reporter_path in reporters:
        reporter_app, reporter_name = load_reporter(reporter_path)

        if reporter_app:
            # Получаем все маршруты из репортера
            for rule in reporter_app.url_map.iter_rules():
                # Копируем маршрут в основное приложение
                endpoint = f"{reporter_name}_{rule.endpoint}"
                view_func = reporter_app.view_functions[rule.endpoint]

                app.add_url_rule(
                    rule.rule,
                    endpoint=endpoint,
                    view_func=view_func,
                    methods=rule.methods
                )

                logging.info(f"✅ Зарегистрирован маршрут: {rule.rule} ({reporter_name})")

            registered_count += 1

    logging.info(f"📊 Зарегистрировано репортеров: {registered_count}/{len(reporters)}")
    return registered_count


def run_scheduler():
    """Запуск планировщика в отдельном потоке"""
    try:
        from scheduler import main as scheduler_main
        logging.info("🚀 Запуск планировщика в фоновом режиме")
        scheduler_main()
    except Exception as e:
        logging.error(f"❌ Ошибка планировщика: {e}")


@app.route('/')
def index():
    """Главная страница с навигацией"""
    reporters = find_reporters()

    # Определяем доступные маршруты
    routes_info = []
    for reporter_path in reporters:
        reporter_app, reporter_name = load_reporter(reporter_path)
        if reporter_app:
            for rule in reporter_app.url_map.iter_rules():
                if rule.endpoint != 'static':  # Исключаем статические файлы
                    routes_info.append({
                        'url': rule.rule,
                        'name': reporter_name.replace('_reporter', '').replace('_', ' ').title(),
                        'methods': list(rule.methods - {'HEAD', 'OPTIONS'})
                    })

    html_template = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CRM Analytics Dashboard</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 25px 50px rgba(0, 0, 0, 0.15);
            overflow: hidden;
        }

        .header {
            background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }

        .header h1 {
            font-size: 3em;
            margin-bottom: 10px;
            font-weight: 300;
        }

        .header p {
            font-size: 1.2em;
            opacity: 0.9;
        }

        .info-section {
            padding: 30px;
            background: #f8f9fa;
            border-bottom: 1px solid #e9ecef;
        }

        .info-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
        }

        .info-card {
            background: white;
            padding: 20px;
            border-radius: 15px;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
            text-align: center;
        }

        .info-card h3 {
            color: #2c3e50;
            margin-bottom: 10px;
        }

        .info-card p {
            color: #6c757d;
            font-size: 0.9em;
        }

        .routes-section {
            padding: 30px;
        }

        .routes-title {
            font-size: 2em;
            color: #2c3e50;
            margin-bottom: 30px;
            text-align: center;
        }

        .routes-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
        }

        .route-card {
            background: white;
            border: 2px solid #e9ecef;
            border-radius: 15px;
            padding: 25px;
            transition: all 0.3s;
            cursor: pointer;
        }

        .route-card:hover {
            border-color: #667eea;
            transform: translateY(-5px);
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.1);
        }

        .route-name {
            font-size: 1.3em;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 10px;
        }

        .route-url {
            background: #f8f9fa;
            padding: 8px 12px;
            border-radius: 8px;
            font-family: monospace;
            color: #667eea;
            margin-bottom: 10px;
            border: 1px solid #e9ecef;
        }

        .route-methods {
            display: flex;
            gap: 5px;
            flex-wrap: wrap;
        }

        .method-badge {
            background: #667eea;
            color: white;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: 600;
        }

        .status-section {
            padding: 20px 30px;
            background: #e8f5e8;
            text-align: center;
            border-top: 1px solid #e9ecef;
        }

        .status-text {
            color: #2e7d32;
            font-weight: 600;
        }

        .footer {
            padding: 20px 30px;
            background: #2c3e50;
            color: white;
            text-align: center;
            font-size: 0.9em;
            opacity: 0.8;
        }

        @media (max-width: 768px) {
            .header h1 {
                font-size: 2em;
            }
            .info-grid, .routes-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 CRM Analytics</h1>
            <p>Центр управления отчетностью и аналитики</p>
        </div>

        <div class="info-section">
            <div class="info-grid">
                <div class="info-card">
                    <h3>🤖 Планировщик</h3>
                    <p>Автоматически собирает данные каждые {{ interval }} минут</p>
                </div>
                <div class="info-card">
                    <h3>📈 Репортеры</h3>
                    <p>{{ reporters_count }} активных модулей отчетности</p>
                </div>
                <div class="info-card">
                    <h3>🔄 Маршруты</h3>
                    <p>{{ routes_count }} доступных эндпоинтов</p>
                </div>
                <div class="info-card">
                    <h3>⏰ Статус</h3>
                    <p>Система запущена {{ current_time }}</p>
                </div>
            </div>
        </div>

        <div class="routes-section">
            <h2 class="routes-title">Доступные отчеты</h2>
            <div class="routes-grid">
                {% for route in routes %}
                <div class="route-card" onclick="window.open('{{ route.url }}', '_blank')">
                    <div class="route-name">{{ route.name }}</div>
                    <div class="route-url">{{ route.url }}</div>
                    <div class="route-methods">
                        {% for method in route.methods %}
                        <span class="method-badge">{{ method }}</span>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>

        <div class="status-section">
            <div class="status-text">✅ Система работает в штатном режиме</div>
        </div>

        <div class="footer">
            CRM Analytics Dashboard • {{ current_time }}
        </div>
    </div>
</body>
</html>
    """

    return render_template_string(
        html_template,
        routes=routes_info,
        reporters_count=len(reporters),
        routes_count=len(routes_info),
        interval=SCHEDULER_CONFIG['interval'] // 60,
        current_time=datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    )


def main():
    """Главная функция приложения"""
    print("=" * 60)
    print("🚀 ЗАПУСК CRM ANALYTICS SYSTEM")
    print("=" * 60)

    # Создаем необходимые папки
    os.makedirs('./reporters', exist_ok=True)
    os.makedirs(SCHEDULER_CONFIG['workers_dir'], exist_ok=True)

    # Проверяем, есть ли репортеры
    reporters = find_reporters()
    if not reporters:
        logging.warning("⚠️ Папка reporters пуста. Создайте файлы репортеров.")
        print("\n📝 Инструкция:")
        print("1. Создайте папку 'reporters'")
        print("2. Поместите в неё файлы *_reporter.py")
        print("3. Перезапустите приложение")

    # Регистрируем репортеры
    registered_count = register_reporters()

    # Запускаем планировщик в отдельном потоке
    if os.path.exists(SCHEDULER_CONFIG['workers_dir']):
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        logging.info("✅ Планировщик запущен в фоновом режиме")
    else:
        logging.warning("⚠️ Папка с воркерами не найдена")

    # Выводим информацию
    print(f"\n📊 Статистика:")
    print(f"   • Репортеров загружено: {registered_count}")
    print(f"   • Веб-сервер: http://{WEB_CONFIG['host']}:{WEB_CONFIG['port']}")
    print(f"   • Планировщик: {'Активен' if os.path.exists(SCHEDULER_CONFIG['workers_dir']) else 'Неактивен'}")
    print("\n🌐 Доступные маршруты:")

    for rule in app.url_map.iter_rules():
        if rule.endpoint != 'static':
            print(f"   • {rule.rule}")

    print(f"\n🔄 Для остановки нажмите Ctrl+C")
    print("=" * 60)

    try:
        # Запускаем веб-сервер
        app.run(**WEB_CONFIG)
    except KeyboardInterrupt:
        logging.info("🛑 Приложение остановлено пользователем")
    except Exception as e:
        logging.error(f"❌ Критическая ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()