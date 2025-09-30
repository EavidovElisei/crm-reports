#!/usr/bin/env python3
"""
Воркер сбора данных - получает данные из CRM и сохраняет в БД
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import requests
import logging
from datetime import datetime, timedelta
from psycopg2.extras import Json
from config import DB_CONFIG, CRM_CONFIG

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - DATA - %(message)s')


def get_token():
    """Получение токена из БД"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("SELECT current_token FROM auth_tokens WHERE service = 'crm'")
    result = cur.fetchone()

    cur.close()
    conn.close()

    if not result or not result[0]:
        raise ValueError("Токен не найден в БД")

    return result[0]


def fetch_crm_data(token, start_date, end_date):
    """Получение данных из CRM"""
    start_timestamp = int(start_date.timestamp() * 1000)
    end_timestamp = int(end_date.timestamp() * 1000)

    payload = {
        'statuses': CRM_CONFIG['statuses'],
        'managerIds': CRM_CONFIG['manager_ids'],
        'startTime': start_timestamp,
        'endTime': end_timestamp
    }

    headers = {
        'Authorization': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    response = requests.post(CRM_CONFIG['api_url'], json=payload, headers=headers)
    response.raise_for_status()

    return response.json()


def save_requests_to_db(requests_data):
    """Сохранение заявок в БД"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    saved_count = 0
    updated_count = 0

    for request in requests_data:
        request_id = request['id']
        created_at = datetime.fromtimestamp(request['created'] / 1000)

        # Проверяем существует ли заявка
        cur.execute("SELECT data FROM requests WHERE id = %s", (request_id,))
        existing = cur.fetchone()

        if existing:
            existing_data = existing[0]
            # Сохраняем enrichment если есть
            enrichment = existing_data.get('enrichment') if isinstance(existing_data, dict) else None

            # Сравниваем только основные данные (без enrichment)
            existing_without_enrichment = existing_data.copy() if isinstance(existing_data, dict) else existing_data
            if isinstance(existing_without_enrichment, dict) and 'enrichment' in existing_without_enrichment:
                del existing_without_enrichment['enrichment']

            if existing_without_enrichment != request:
                # Обновляем данные, но сохраняем enrichment
                new_data = request.copy()
                if enrichment:
                    new_data['enrichment'] = enrichment

                cur.execute("""
                    UPDATE requests 
                    SET data = %s, updated_at = %s 
                    WHERE id = %s
                """, (Json(new_data), datetime.now(), request_id))
        else:
            # Вставляем новую заявку
            cur.execute("""
                INSERT INTO requests (id, created_at, data) 
                VALUES (%s, %s, %s)
            """, (request_id, created_at, Json(request)))
            saved_count += 1

    conn.commit()
    cur.close()
    conn.close()

    return saved_count, updated_count

def main():
    """Основная функция воркера"""
    try:
        logging.info("📊 Запуск воркера сбора данных")

        # Получаем токен
        token = get_token()
        logging.info("✅ Токен получен из БД")

        # Определяем период (текущий месяц с начала до конца)
        now = datetime.now()
        end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        logging.info(f"Период: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}")

        # Получаем данные из CRM
        data = fetch_crm_data(token, start_date, end_date)
        logging.info(f"Получено {len(data)} заявок из CRM")

        # Сохраняем в БД
        saved, updated = save_requests_to_db(data)
        logging.info(f"✅ Сохранено: {saved} новых, {updated} обновлено")

        return True

    except Exception as e:
        logging.error(f"❌ Ошибка в воркере сбора данных: {e}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)