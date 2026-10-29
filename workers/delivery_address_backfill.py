#!/usr/bin/env python3
"""
Догоняет адреса доставки ККТ из CRM install (поле kkmDeliveryAddress).

Список заявок list/read адрес не содержит — он только в карточке install.
Обычный enrichment смотрит последние 30 дней, поэтому старые отправки остаются без адреса.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import psycopg2
from psycopg2.extras import Json

from config import DB_CONFIG
from enrichment_worker import (
    _extract_delivery_address_from_install,
    _extract_delivery_city_from_install,
    fetch_install_data,
    get_token,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - ADDR_BACKFILL - %(message)s")

MAX_WORKERS = int(os.environ.get("ADDR_BACKFILL_WORKERS", "10"))
YEAR_FROM = os.environ.get("ADDR_BACKFILL_YEAR", "2026")


def get_missing_shipments():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, data
        FROM requests
        WHERE data->>'sentToDeliveryDate' IS NOT NULL
          AND data->>'sentToDeliveryDate' ~ '^[0-9]+$'
          AND to_timestamp((data->>'sentToDeliveryDate')::bigint / 1000.0)
              >= %s::timestamp
          AND COALESCE(data->>'status', '') NOT IN (
              'CANCELED_BY_CLIENT', 'CANCELED_BY_BANK', 'REFUND'
          )
          AND COALESCE(NULLIF(data#>>'{enrichment,delivery_address}', ''), '') = ''
        ORDER BY id
        """,
        (f"{YEAR_FROM}-01-01",),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def save_address(request_id, current_data, address, city):
    data = current_data.copy() if isinstance(current_data, dict) else {}
    enrichment = data.get("enrichment") if isinstance(data.get("enrichment"), dict) else {}
    enrichment = dict(enrichment)
    if address:
        enrichment["delivery_address"] = address
    if city:
        enrichment["delivery_city"] = city
    data["enrichment"] = enrichment

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        "UPDATE requests SET data = %s, updated_at = %s WHERE id = %s",
        (Json(data), datetime.now(), request_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def process_one(token, item):
    request_id, current_data = item
    install_data = fetch_install_data(token, request_id)
    if not install_data:
        return request_id, False, "no_install"
    address = _extract_delivery_address_from_install(install_data)
    city = _extract_delivery_city_from_install(install_data)
    if not address:
        return request_id, False, "empty_address"
    save_address(request_id, current_data, address, city)
    return request_id, True, "ok"


def main():
    logging.info("Заполнение адресов доставки из CRM install")
    token = get_token()
    rows = get_missing_shipments()
    logging.info("Без адреса: %s заявок, workers=%s", len(rows), MAX_WORKERS)
    if not rows:
        return True

    ok = 0
    empty = 0
    fail = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_one, token, item): item[0] for item in rows}
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                _rid, success, reason = future.result()
                if success:
                    ok += 1
                elif reason == "empty_address":
                    empty += 1
                else:
                    fail += 1
            except Exception as e:
                fail += 1
                logging.error("Ошибка заявки %s: %s", futures[future], e)
            if done % 200 == 0 or done == len(rows):
                logging.info("Прогресс %s/%s: ok=%s empty=%s fail=%s", done, len(rows), ok, empty, fail)

    logging.info("Готово: записано %s, пустой адрес в CRM %s, ошибок %s", ok, empty, fail)
    return fail == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
