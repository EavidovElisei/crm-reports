#!/usr/bin/env python3
"""
Планировщик - запускает воркеры по расписанию
"""
import os
import sys
import time
import subprocess
import logging
from datetime import datetime
import glob
from config import SCHEDULER_CONFIG

# Настройка логирования с поддержкой UTF-8
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - SCHEDULER - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)


def detect_python_command():
    """Автоматическое определение команды Python"""
    # При запуске из virtualenv (./.venv, ./venv) берём его интерпретатор:
    # системный python не знает про установленные зависимости (psycopg2, requests и т.д.)
    if sys.prefix != sys.base_prefix and sys.executable:
        logging.info(f"✅ Используем интерпретатор virtualenv: {sys.executable}")
        return sys.executable

    commands = ['python', 'python3', 'py']

    for cmd in commands:
        try:
            result = subprocess.run([cmd, '--version'], capture_output=True, text=True)
            if result.returncode == 0:
                logging.info(f"✅ Найдена команда Python: {cmd}")
                return cmd
        except FileNotFoundError:
            continue

    # Если ничего не найдено, используем sys.executable
    logging.warning("⚠️ Стандартные команды Python не найдены, используем sys.executable")
    return sys.executable


def find_workers():
    """Поиск воркеров в папке

    Периодические воркеры запускаются в порядке SCHEDULER_CONFIG['worker_order']:
    сначала токен, затем заявки, обогащение и аналитика.
    Остальные *_worker.py (разовые скрипты вроде batch_enrichment_worker.py)
    автоматически не запускаются.
    """
    workers_dir = SCHEDULER_CONFIG['workers_dir']
    ordered = []
    for name in SCHEDULER_CONFIG.get('worker_order', []):
        path = os.path.join(workers_dir, name)
        if os.path.exists(path):
            ordered.append(path)
        else:
            logging.warning(f"⚠️ Воркер из worker_order не найден: {name}")

    if ordered:
        return ordered

    # Фолбэк, если порядок не задан
    return sorted(glob.glob(os.path.join(workers_dir, '*_worker.py')))


def run_worker(worker_path, python_cmd):
    """Запуск одного воркера"""
    worker_name = os.path.basename(worker_path)

    try:
        logging.info(f"🚀 Запуск воркера: {worker_name}")

        # Настраиваем окружение для воркера
        env = os.environ.copy()
        project_root = os.path.dirname(os.path.abspath(__file__))
        env['PYTHONPATH'] = project_root
        
        # Настраиваем прокси для доступа к внешним API
        # (на боевом сервере прокси обязателен, локально его можно отключить в config_local.py)
        proxy = SCHEDULER_CONFIG.get('proxy')
        if proxy:
            if 'HTTP_PROXY' not in env:
                env['HTTP_PROXY'] = proxy
            if 'HTTPS_PROXY' not in env:
                env['HTTPS_PROXY'] = proxy
        
        # Запускаем воркер без захвата вывода (позволяем ему логировать самостоятельно)
        result = subprocess.run(
            [python_cmd, worker_path],
            env=env,
            timeout=300  # Таймаут 5 минут
        )

        if result.returncode == 0:
            logging.info(f"✅ Воркер {worker_name} выполнен успешно")
        else:
            logging.error(f"❌ Воркер {worker_name} завершился с ошибкой (код: {result.returncode})")

        return result.returncode == 0

    except subprocess.TimeoutExpired:
        logging.error(f"⏰ Воркер {worker_name} превысил таймаут")
        return False
    except FileNotFoundError as e:
        logging.error(f"❌ Команда Python не найдена: {e}")
        return False
    except Exception as e:
        logging.error(f"❌ Ошибка запуска воркера {worker_name}: {e}")
        return False


def run_all_workers():
    """Запуск всех воркеров"""
    workers = find_workers()

    if not workers:
        logging.warning("⚠️ Воркеры не найдены")
        return

    logging.info(f"📋 Найдено воркеров: {len(workers)}")

    # Определяем команду Python один раз
    python_cmd = detect_python_command()

    success_count = 0

    for worker in workers:
        if run_worker(worker, python_cmd):
            success_count += 1

        # Небольшая пауза между воркерами
        time.sleep(2)

    logging.info(f"📊 Выполнено успешно: {success_count}/{len(workers)}")


def main():
    """Основной цикл планировщика"""
    logging.info("⏰ Запуск планировщика")
    logging.info(f"Папка с воркерами: {SCHEDULER_CONFIG['workers_dir']}")
    logging.info(f"Интервал: {SCHEDULER_CONFIG['interval']} секунд ({SCHEDULER_CONFIG['interval'] // 60} минут)")

    # Создаем папку для воркеров если не существует
    os.makedirs(SCHEDULER_CONFIG['workers_dir'], exist_ok=True)

    try:
        while True:
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logging.info(f"🔄 Начало цикла: {current_time}")

            # Запускаем всех воркеров
            run_all_workers()

            # Ждем до следующего запуска
            logging.info(f"😴 Ожидание {SCHEDULER_CONFIG['interval']} секунд до следующего запуска")
            time.sleep(SCHEDULER_CONFIG['interval'])

    except KeyboardInterrupt:
        logging.info("🛑 Планировщик остановлен пользователем")
    except Exception as e:
        logging.error(f"❌ Критическая ошибка планировщика: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()