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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from psycopg2.extras import Json
from config import DB_CONFIG, CRM_CONFIG
from last_comment import (
    build_status_fallback_comment,
    fetch_bank_request_bundle,
    resolve_last_comment,
)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - ENRICHMENT - %(message)s')

# Интервал обновления данных в часах
UPDATE_INTERVAL_HOURS = 0.5
# Параллелизм: можно управлять через переменную окружения ENRICH_MAX_WORKERS
MAX_WORKERS = int(os.environ.get('ENRICH_MAX_WORKERS', '12'))


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


def get_requests_missing_delivery_address(limit=80):
    """Старые отправки без адреса: list/read его не содержит, нужен install."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, data
        FROM requests
        WHERE data->>'sentToDeliveryDate' IS NOT NULL
          AND COALESCE(NULLIF(data#>>'{enrichment,delivery_address}', ''), '') = ''
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


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


def _extract_appointment_from_install(install_data):
    """Извлекает дату записи на регистрацию из install-ответа.3 

    Поддерживает поле 'yClientRecordDate' (мс) если присутствует.
    Возвращает timestamp в мс или None.
    """
    if not isinstance(install_data, dict):
        return None
    value = install_data.get('yClientRecordDate')
    return value if isinstance(value, (int, float)) else None


def _extract_delivery_address_from_install(install_data):
    """Полный адрес доставки ККТ из ответа install API (kkmDeliveryAddress)."""
    if not isinstance(install_data, dict):
        return None
    value = install_data.get('kkmDeliveryAddress')
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _city_from_delivery_address(address):
    """Населённый пункт из строки адреса доставки ККТ."""
    if not isinstance(address, str) or not address.strip():
        return None
    parts = [p.strip() for p in address.split(',') if p.strip()]
    prefixes = (
        'г ', 'г.', 'город ',
        'п ', 'п.', 'посёлок ', 'поселок ', 'рп ',
        'с ', 'с.', 'село ',
        'деревня ', 'ст-ца ', 'станица ',
        'аул ', 'хутор ',
    )
    for part in parts:
        low = part.lower()
        if low.startswith('д.') or low.startswith('д '):
            rest = part.split('.', 1)[-1].strip() if '.' in part else part[1:].strip()
            if rest[:1].isdigit():
                continue
        if any(low.startswith(prefix) for prefix in prefixes):
            return part
    return None


def _extract_delivery_city_from_install(install_data):
    """Город получателя из ответа install API."""
    if not isinstance(install_data, dict):
        return None

    candidates = (
        'deliveryCity',
        'delivery_city',
        'recipientCity',
        'clientCity',
        'city',
        'cityName',
        'organizationCity',
        'deliveryAddressCity',
    )

    def from_mapping(obj):
        if not isinstance(obj, dict):
            return None
        for key in candidates:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                for nested_key in ('name', 'title', 'value', 'city', 'cityName'):
                    nested = value.get(nested_key)
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
        for nest_key in ('delivery', 'deliveryAddress', 'address', 'recipient', 'client', 'organization'):
            nest = obj.get(nest_key)
            if isinstance(nest, dict):
                found = from_mapping(nest)
                if found:
                    return found
                for nested_key in ('city', 'cityName', 'name', 'settlement', 'town'):
                    nested = nest.get(nested_key)
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
        return None

    found = from_mapping(install_data)
    if found:
        return found
    return _city_from_delivery_address(_extract_delivery_address_from_install(install_data))


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


def fetch_kkm_device(token, device_id):
    """Статус кассы/доставки по boxed_kkm_device.id (kkmId из install)."""
    if not device_id:
        return None
    url = f"{CRM_CONFIG['base_url']}/admin-api/protected/boxed_kkm_device/{device_id}"
    headers = {
        'Authorization': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.warning(f"Ошибка получения устройства {device_id}: {e}")
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

    # 1. Получаем данные установки для извлечения ID заказов и актуального статуса/дат
    install_data = fetch_install_data(token, request_id)
    order_id = None
    additional_order_id = None

    if install_data:
        order_id = install_data.get('orderId')
        additional_order_id = install_data.get('additionalOrderId')
        # Обновляем статус заявки из install-ответа, если он присутствует
        install_status = install_data.get('status')
        if install_status and isinstance(current_data, dict):
            current_data['status'] = install_status
        # Извлекаем дату записи на регистрацию, если доступна
        appointment_from_install = _extract_appointment_from_install(install_data)
        if appointment_from_install:
            enrichment["registration_appointment_date"] = appointment_from_install

        delivery_city = _extract_delivery_city_from_install(install_data)
        if delivery_city:
            enrichment["delivery_city"] = delivery_city

        delivery_address = _extract_delivery_address_from_install(install_data)
        if delivery_address:
            enrichment["delivery_address"] = delivery_address

        device_id = install_data.get('kkmId')
        if device_id:
            enrichment["kkm_device_id"] = device_id
            device = fetch_kkm_device(token, device_id)
            if isinstance(device, dict) and device.get('status'):
                enrichment["delivery_status"] = device.get('status')

    # 2. Получаем информацию о регистрации
    registration_info = fetch_registration_info(token, request_id)
    if registration_info:
        fns_time = registration_info.get('fnsRegistrationTime')
        if fns_time:
            enrichment["fns_registration_status"] = "registered"
            enrichment["fns_registration_time"] = fns_time
        else:
            enrichment["fns_registration_status"] = "not_registered"

    # 3. Дата записи уже извлечена из install (если доступна)

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

    order_for_lc = current_data if isinstance(current_data, dict) else {}
    existing_en = (
        current_data.get("enrichment")
        if isinstance(current_data, dict) and isinstance(current_data.get("enrichment"), dict)
        else {}
    )
    try:
        enrichment["last_comment"] = resolve_last_comment(
            token, int(request_id), install_data, order_for_lc
        )
    except Exception as e:
        logging.warning("last_comment для заявки %s: %s", request_id, e)
        enrichment["last_comment"] = build_status_fallback_comment(order_for_lc)

    try:
        enrichment["bank_request"] = fetch_bank_request_bundle(token, int(request_id))
    except Exception as e:
        logging.warning("bank_request для заявки %s: %s", request_id, e)
        if isinstance(existing_en.get("bank_request"), dict):
            enrichment["bank_request"] = existing_en["bank_request"]

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
        seen = {item[0] for item in requests_data}
        for item in get_requests_missing_delivery_address():
            if item[0] not in seen:
                requests_data.append(item)
                seen.add(item[0])
        logging.info(f"📋 Найдено {len(requests_data)} заявок для обогащения")

        # Вычисляем время границы для логирования
        threshold_time = datetime.now() - timedelta(hours=UPDATE_INTERVAL_HOURS)
        logging.info(
            f"🕒 Обогащаем заявки старше {UPDATE_INTERVAL_HOURS} часов ({threshold_time.strftime('%d.%m.%Y %H:%M:%S')})")

        enriched_count = 0
        error_count = 0

        def process_one(item):
            request_id, current_data = item
            logging.info(f"🔄 Обогащение заявки {request_id}")
            enriched_data = enrich_request_data(token, request_id, current_data)
            save_enriched_data(request_id, enriched_data)
            return request_id

        # Параллельная обработка заявок
        total = len(requests_data)
        logging.info(f"🧵 Запуск параллельной обработки: workers={MAX_WORKERS}, всего заявок={total}")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_one, item): item[0] for item in requests_data}
            for future in as_completed(futures):
                rid = futures[future]
                try:
                    _ = future.result()
                    enriched_count += 1
                except Exception as e:
                    error_count += 1
                    logging.error(f"❌ Ошибка обогащения заявки {rid}: {e}")

        logging.info(f"🎉 Обогащение завершено: {enriched_count} успешно, {error_count} ошибок (всего {total})")
        return True

    except Exception as e:
        logging.error(f"❌ Критическая ошибка в воркере обогащения: {e}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)