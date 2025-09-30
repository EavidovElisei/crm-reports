#!/usr/bin/env python3
"""
Воркер обогащения данных - получает дополнительные данные для заявок из CRM
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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - ENRICHMENT - %(message)s')

# Интервал обновления данных в часах
UPDATE_INTERVAL_HOURS = 3


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


def get_requests_for_enrichment():
    """Получение заявок за последние 30 дней из БД, которые нуждаются в обогащении"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Получаем заявки за последние 30 дней
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)

    # Время границы для обновления (UPDATE_INTERVAL_HOURS часов назад в миллисекундах)
    update_threshold = int((datetime.now() - timedelta(hours=UPDATE_INTERVAL_HOURS)).timestamp() * 1000)

    cur.execute("""
        SELECT id, data 
        FROM requests 
        WHERE created_at >= %s AND created_at <= %s
        AND (
            data->'enrichment' IS NULL 
            OR data#>>'{enrichment,enriched_at}' IS NULL
            OR CAST(data#>>'{enrichment,enriched_at}' AS BIGINT) < %s
        )
        ORDER BY created_at DESC
    """, (start_date, end_date, update_threshold))

    requests_data = cur.fetchall()
    cur.close()
    conn.close()

    return requests_data


def fetch_install_data(token, request_id):
    """Получение данных установки для получения ID заказов"""
    url = f"{CRM_CONFIG['base_url']}/admin-api/protected/boxed_kkm/install/{request_id}"

    headers = {
        'Authorization': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.warning(f"Ошибка получения данных установки для заявки {request_id}: {e}")
        return None


def fetch_registration_info(token, request_id):
    """Получение информации о регистрации кассы"""
    url = f"{CRM_CONFIG['base_url']}/admin-api/protected/boxed_kkm/{request_id}/registration/info"

    headers = {
        'Authorization': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.warning(f"Ошибка получения информации о регистрации для заявки {request_id}: {e}")
        return None


def fetch_appointment_date(token, request_id):
    """Получение даты записи на регистрацию"""
    url = f"{CRM_CONFIG['base_url']}/admin-api/protected/boxed_kkm/install/yclients/date/{request_id}"

    headers = {
        'Authorization': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        # Проверяем, что ответ не пустой (пустой ответ = нет записи)
        if not response.text.strip():
            return None

        # Ответ может быть просто числом (timestamp) или JSON
        try:
            return response.json()
        except ValueError:
            # Если не JSON, пробуем как обычное число
            try:
                return int(response.text.strip())
            except ValueError:
                logging.warning(f"Неожиданный формат ответа для даты записи заявки {request_id}: {response.text}")
                return None

    except requests.exceptions.RequestException as e:
        logging.warning(f"Ошибка получения даты записи для заявки {request_id}: {e}")
        return None


def fetch_order_info(token, order_id):
    """Получение информации о заказе/счете"""
    url = f"{CRM_CONFIG['base_url']}/admin-api/protected/income"

    headers = {
        'Authorization': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    params = {'order_id': order_id}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.warning(f"Ошибка получения информации о заказе {order_id}: {e}")
        return None


def enrich_request_data(token, request_id, current_data):
    """Обогащение данных заявки"""
    enrichment = {
        "fns_registration_status": "unknown",
        "fns_registration_time": None,
        "registration_appointment_date": None,
        "main_order": None,
        "additional_order": None,
        "enriched_at": int(datetime.now().timestamp() * 1000)
    }

    # 1. Получаем данные установки для извлечения ID заказов
    install_data = fetch_install_data(token, request_id)
    order_id = None
    additional_order_id = None

    if install_data:
        order_id = install_data.get('orderId')
        additional_order_id = install_data.get('additionalOrderId')

    # 2. Получаем информацию о регистрации
    registration_info = fetch_registration_info(token, request_id)
    if registration_info:
        fns_time = registration_info.get('fnsRegistrationTime')
        if fns_time:
            enrichment["fns_registration_status"] = "registered"
            enrichment["fns_registration_time"] = fns_time
        else:
            enrichment["fns_registration_status"] = "not_registered"

    # 3. Получаем дату записи на регистрацию
    appointment_date = fetch_appointment_date(token, request_id)
    if appointment_date:
        enrichment["registration_appointment_date"] = appointment_date

    # 4. Получаем информацию об основном заказе
    if order_id:
        main_order_info = fetch_order_info(token, order_id)
        if main_order_info:
            paid_time = main_order_info.get('paidTime')
            enrichment["main_order"] = {
                "order_id": order_id,
                "amount": main_order_info.get('amount'),
                "paid_time": paid_time,
                "status": "paid" if paid_time else "unpaid"
            }
        else:
            enrichment["main_order"] = {
                "order_id": order_id,
                "amount": None,
                "paid_time": None,
                "status": "no_data"
            }

    # 5. Получаем информацию о дополнительном заказе
    if additional_order_id:
        additional_order_info = fetch_order_info(token, additional_order_id)
        if additional_order_info:
            paid_time = additional_order_info.get('paidTime')
            enrichment["additional_order"] = {
                "order_id": additional_order_id,
                "amount": additional_order_info.get('amount'),
                "paid_time": paid_time,
                "status": "paid" if paid_time else "unpaid"
            }
        else:
            enrichment["additional_order"] = {
                "order_id": additional_order_id,
                "amount": None,
                "paid_time": None,
                "status": "no_data"
            }
    else:
        enrichment["additional_order"] = {
            "order_id": None,
            "amount": None,
            "paid_time": None,
            "status": "not_exists"
        }

    # Добавляем обогащенные данные к существующим
    enriched_data = current_data.copy()
    enriched_data["enrichment"] = enrichment

    return enriched_data


def save_enriched_data(request_id, enriched_data):
    """Сохранение обогащенных данных в БД"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("""
        UPDATE requests 
        SET data = %s, updated_at = %s 
        WHERE id = %s
    """, (Json(enriched_data), datetime.now(), request_id))

    rows_affected = cur.rowcount
    if rows_affected == 0:
        logging.warning(f"⚠️ Заявка {request_id} не найдена в БД для обновления!")

    conn.commit()
    cur.close()
    conn.close()


def main():
    """Основная функция воркера обогащения"""
    try:
        logging.info("🔍 Запуск воркера обогащения данных")

        # Получаем токен
        token = get_token()
        logging.info("✅ Токен получен из БД")

        # Получаем заявки для обогащения
        requests_data = get_requests_for_enrichment()
        logging.info(f"📋 Найдено {len(requests_data)} заявок для обогащения")

        # Вычисляем время границы для логирования
        threshold_time = datetime.now() - timedelta(hours=UPDATE_INTERVAL_HOURS)
        logging.info(
            f"🕒 Обогащаем заявки старше {UPDATE_INTERVAL_HOURS} часов ({threshold_time.strftime('%d.%m.%Y %H:%M:%S')})")

        enriched_count = 0
        error_count = 0

        for request_id, current_data in requests_data:
            try:
                logging.info(f"🔄 Обогащение заявки {request_id}")

                # Обогащаем данные
                enriched_data = enrich_request_data(token, request_id, current_data)

                # Сохраняем в БД
                save_enriched_data(request_id, enriched_data)

                enriched_count += 1
                logging.info(f"✅ Заявка {request_id} обогащена")

            except Exception as e:
                error_count += 1
                logging.error(f"❌ Ошибка обогащения заявки {request_id}: {e}")
                continue

        logging.info(f"🎉 Обогащение завершено: {enriched_count} успешно, {error_count} ошибок")
        return True

    except Exception as e:
        logging.error(f"❌ Критическая ошибка в воркере обогащения: {e}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)