#!/usr/bin/env python3
"""
Воркер для сбора данных аналитики из admin-api
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import json
import requests
import hashlib
import base64
import logging
from datetime import datetime, timedelta
from config import DB_CONFIG

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - ANALYTICS_WORKER - %(message)s')

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


def save_analytics_to_db(analytics_data, start_date, end_date):
    """Сохранение данных аналитики в БД"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    # Проверяем есть ли уже данные за этот период
    cur.execute("""
        SELECT id FROM analytics_data 
        WHERE period_start = %s AND period_end = %s
    """, (start_date, end_date))
    
    existing = cur.fetchone()
    
    if existing:
        # Обновляем существующую запись
        cur.execute("""
            UPDATE analytics_data 
            SET data = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (json.dumps(analytics_data), existing[0]))
        logging.info(f"✅ Данные аналитики обновлены (ID: {existing[0]})")
    else:
        # Создаем новую запись
        cur.execute("""
            INSERT INTO analytics_data (period_start, period_end, data)
            VALUES (%s, %s, %s)
        """, (start_date, end_date, json.dumps(analytics_data)))
        logging.info("✅ Данные аналитики сохранены в БД")
    
    conn.commit()
    cur.close()
    conn.close()


def cleanup_old_analytics():
    """Очистка старых данных аналитики (оставляем только последние 30 записей)"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    cur.execute("""
        DELETE FROM analytics_data 
        WHERE id NOT IN (
            SELECT id FROM analytics_data 
            ORDER BY created_at DESC 
            LIMIT 30
        )
    """)
    
    deleted_count = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    
    if deleted_count > 0:
        logging.info(f"🧹 Удалено {deleted_count} старых записей аналитики")


def main():
    """Основная функция воркера"""
    try:
        logging.info("🚀 Запуск воркера сбора аналитики...")
        
        # Определяем период (последняя неделя)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        # Получаем данные аналитики
        logging.info("📊 Получение данных аналитики...")
        analytics_data = get_analytics_data(
            str(int(start_date.timestamp() * 1000)),
            str(int(end_date.timestamp() * 1000))
        )
        
        logging.info("✅ Данные аналитики получены успешно:")
        logging.info(f"  - Заявок за период: {analytics_data.get('requestsPerPeriod', '—')}")
        logging.info(f"  - Заявок за неделю: {analytics_data.get('requestsPerWeek', '—')}")
        logging.info(f"  - Конверсия: {analytics_data.get('conversion', 0):.1%}")
        logging.info(f"  - Очередь регистрации: {analytics_data.get('registrationQueue', '—')}")
        logging.info(f"  - Остаток ФН: {analytics_data.get('fnRemains', '—')}")
        
        # Сохраняем в БД
        logging.info("💾 Сохранение данных в БД...")
        save_analytics_to_db(analytics_data, start_date, end_date)
        
        # Очищаем старые данные
        logging.info("🧹 Очистка старых данных...")
        cleanup_old_analytics()
        
        logging.info("✅ Воркер завершен успешно")
        
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Ошибка HTTP запроса: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logging.error(f"   Статус код: {e.response.status_code}")
            logging.error(f"   Ответ сервера: {e.response.text}")
        return 1
        
    except psycopg2.Error as e:
        logging.error(f"❌ Ошибка БД: {e}")
        return 1
        
    except Exception as e:
        logging.error(f"❌ Общая ошибка: {e}")
        return 1
    
    return 0


if __name__ == '__main__':
    exit(main()) 