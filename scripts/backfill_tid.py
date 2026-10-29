#!/usr/bin/env python3
"""
Разовая догрузка TID в локальную базу.

В requests.data поле deviceInfo[0].tid при синхронизации часто пустое,
поэтому TID тянем из карточек установки CRM и сохраняем в отдельную
таблицу request_tids — её потом читает отчёт /sla/bank.

Запуск (из корня проекта):
    .venv/bin/python scripts/backfill_tid.py            # только отсутствующие
    .venv/bin/python scripts/backfill_tid.py --all      # перезапросить все
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import psycopg2
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CRM_CONFIG, DB_CONFIG  # noqa: E402
from reporters.sla_bank_reporter import extract_tid, get_crm_token  # noqa: E402

WORKERS = 12

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS request_tids (
    request_id bigint PRIMARY KEY,
    tid        text   NOT NULL,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);
GRANT SELECT, INSERT, UPDATE, DELETE ON request_tids TO crm_reports_user;
"""


def fetch_tid(token, request_id):
    """TID из карточки установки по номеру заявки."""
    url = (
        CRM_CONFIG['base_url'].rstrip('/')
        + f'/admin-api/protected/boxed_kkm/install/{request_id}'
    )
    headers = {'Authorization': token, 'Accept': 'application/json'}
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return request_id, None
    return request_id, extract_tid(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--all', action='store_true', help='перезапросить все заявки')
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(CREATE_TABLE)
    conn.commit()

    query = """
        SELECT r.id
        FROM requests r
        WHERE r.data IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM request_tids t WHERE t.request_id = r.id
          )
    """
    if args.all:
        query = """
            SELECT r.id
            FROM requests r
            WHERE r.data IS NOT NULL
              AND COALESCE(r.data->'request'->'deviceInfo'->0->>'tid', '') = ''
        """
    cur.execute(query)
    request_ids = [row[0] for row in cur.fetchall()]
    print(f'Заявок к обработке: {len(request_ids)}')
    if not request_ids:
        return

    token = get_crm_token()
    found = 0
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch_tid, token, rid) for rid in request_ids]
        for future in as_completed(futures):
            done += 1
            request_id, tid = future.result()
            if tid:
                found += 1
                cur.execute(
                    """
                    INSERT INTO request_tids (request_id, tid, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (request_id) DO UPDATE
                    SET tid = EXCLUDED.tid,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (request_id, tid),
                )
            if done % 500 == 0:
                conn.commit()
                print(f'  обработано {done}/{len(request_ids)}, TID найдено {found}')

    conn.commit()
    cur.close()
    conn.close()
    print(f'Готово: обработано {done}, TID сохранено {found} ({datetime.now():%H:%M:%S})')


if __name__ == '__main__':
    main()