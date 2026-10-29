#!/usr/bin/env python3
"""
Пакетный воркер для принудительного обогащения заявок банковскими комментариями
Обрабатывает заявки небольшими пакетами для избежания блокировок БД
"""
import os
import sys
import psycopg2
import requests
import logging
import time
from datetime import datetime, timedelta
from psycopg2.extras import Json

# Добавляем корневую папку проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_CONFIG
from last_comment import empty_bank_request_bundle, fetch_bank_request_bundle

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - BATCH_ENRICHMENT - %(message)s')

# Размер пакета для обработки
BATCH_SIZE = 50
# Пауза между пакетами (секунды)
BATCH_DELAY = 2


def get_token():
    """Получение токена из БД"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("SELECT current_token FROM auth_tokens WHERE service = 'crm'")
        result = cur.fetchone()
        if not result or not result[0]:
            raise ValueError("Токен не найден в БД")
        return result[0]
    finally:
        cur.close()
        conn.close()


def get_requests_batch(offset=0, limit=BATCH_SIZE):
    """Получение пакета заявок для обогащения"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        # Получаем заявки: последние 6 месяцев или с начала текущего года (что больше)
        # Это гарантирует обработку всех данных за текущий год, включая 2026 год
        end_date = datetime.now()
        six_months_ago = end_date - timedelta(days=180)
        start_of_year = datetime(end_date.year, 1, 1)
        start_date = min(six_months_ago, start_of_year)  # Берем более раннюю дату
        
        # Время границы для обновления банковских данных (3 часа назад в миллисекундах)
        # Такой же интервал как в enrichment_worker.py
        update_threshold = int((datetime.now() - timedelta(hours=3)).timestamp() * 1000)

        cur.execute("""
            SELECT id, data 
            FROM requests 
            WHERE created_at >= %s AND created_at <= %s
            AND (
                data->'enrichment'->'bank_request' IS NULL
                OR data->'enrichment'->'bank_request'->>'latest_comment' IS NULL
                OR CAST(data#>>'{enrichment,enriched_at}' AS BIGINT) < %s
            )
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (start_date, end_date, update_threshold, limit, offset))

        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def fetch_bank_requests(token, request_id):
    """Получение банковских запросов с комментариями для заявки"""
    try:
        return fetch_bank_request_bundle(token, request_id)
    except requests.exceptions.RequestException as e:
        logging.warning(f"Ошибка получения банковских запросов для заявки {request_id}: {e}")
        return empty_bank_request_bundle()


def update_request_batch(updates):
    """Пакетное обновление заявок"""
    if not updates:
        return 0
        
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    try:
        updated_count = 0
        for request_id, enriched_data in updates:
            cur.execute("""
                UPDATE requests 
                SET data = %s, updated_at = %s 
                WHERE id = %s
            """, (Json(enriched_data), datetime.now(), request_id))
            
            if cur.rowcount > 0:
                updated_count += 1
        
        conn.commit()
        return updated_count
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Ошибка пакетного обновления: {e}")
        return 0
    finally:
        cur.close()
        conn.close()


def process_batch(token, batch_data):
    """Обработка пакета заявок"""
    updates = []
    
    for request_id, current_data in batch_data:
        try:
            # Получаем банковские данные
            bank_request_info = fetch_bank_requests(token, request_id)
            
            # Создаем обновленные данные
            enriched_data = current_data.copy()
            
            # Инициализируем enrichment если его нет
            if 'enrichment' not in enriched_data:
                enriched_data['enrichment'] = {}
            
            # Добавляем банковские данные
            enriched_data['enrichment']['bank_request'] = bank_request_info
            enriched_data['enrichment']['enriched_at'] = int(datetime.now().timestamp() * 1000)
            
            updates.append((request_id, enriched_data))
            
        except Exception as e:
            logging.error(f"Ошибка обработки заявки {request_id}: {e}")
            continue
    
    # Пакетное обновление в БД
    updated_count = update_request_batch(updates)
    return updated_count, len(updates)


def main():
    """Основная функция пакетного обогащения"""
    try:
        logging.info("🔍 Запуск пакетного обогащения заявок банковскими комментариями")
        logging.info(f"📦 Размер пакета: {BATCH_SIZE}, пауза между пакетами: {BATCH_DELAY}с")
        
        # Вычисляем время границы для логирования
        threshold_time = datetime.now() - timedelta(hours=3)
        logging.info(f"🕒 Обновляем банковские данные старше 3 часов ({threshold_time.strftime('%d.%m.%Y %H:%M:%S')})")

        # Получаем токен
        token = get_token()
        logging.info("✅ Токен получен из БД")

        total_processed = 0
        total_updated = 0
        batch_number = 0
        
        while True:
            # Получаем следующий пакет
            batch_data = get_requests_batch(offset=batch_number * BATCH_SIZE)
            
            if not batch_data:
                logging.info("📋 Больше заявок для обработки не найдено")
                break
            
            batch_number += 1
            logging.info(f"📦 Обработка пакета #{batch_number} ({len(batch_data)} заявок)")
            
            # Обрабатываем пакет
            updated_count, processed_count = process_batch(token, batch_data)
            
            total_processed += processed_count
            total_updated += updated_count
            
            logging.info(f"✅ Пакет #{batch_number}: обновлено {updated_count}/{processed_count}")
            logging.info(f"📊 Общий прогресс: {total_updated}/{total_processed}")
            
            # Пауза между пакетами
            if len(batch_data) == BATCH_SIZE:  # Есть еще данные
                logging.info(f"⏸️ Пауза {BATCH_DELAY}с перед следующим пакетом...")
                time.sleep(BATCH_DELAY)

        logging.info(f"🎉 Пакетное обогащение завершено:")
        logging.info(f"   • Всего обработано: {total_processed}")
        logging.info(f"   • Успешно обновлено: {total_updated}")
        logging.info(f"   • Пакетов обработано: {batch_number}")
        
        return True

    except Exception as e:
        logging.error(f"❌ Критическая ошибка в пакетном обогащении: {e}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 