#!/usr/bin/env python3
"""
Общие настройки для всех компонентов системы
"""
import os

# Конфигурация БД
DB_CONFIG = {
    'host': 'localhost',
    'database': 'crm_reports',
    'user': 'crm_reports_user',
    'password': 'crm_reports_password'
}

# Конфигурация CRM
CRM_CONFIG = {
    'auth_url': 'https://kassa.bifit.com/admin-api/oauth/token',
    'api_url': 'https://kassa.bifit.com/admin-api/protected/boxed_kkm/install/list/read',
    'base_url': 'https://kassa.bifit.com',
    'admin_url': 'https://kassa.bifit.com/admin/#/main/alfabank_request/list',
    # Аналитика admin-api (тот же хост, что и CRM; переопределите через CRM_ANALYTICS_URL при необходимости)
    'analytics_url': os.environ.get(
        'CRM_ANALYTICS_URL',
        'https://kassa.bifit.com/admin-api/protected/boxed_kkm/analytics',
    ),
    'client_id': 'cashdesk-rest-client',
    'client_secret': 'cashdesk-rest-client',
    # Актуальный список ID менеджеров
    'manager_ids': [307, 22, 281, 104, 304, 291, 285, 234, 61],
    'statuses': [
        'NEW',
        'DRAFT',
        'INCOME_CREATED',
        'INCOME_PAID',
        'INCOME_PARTIALLY_PAID',
        'KKT_LINKED',
        'COMPLETED',
        'REFUND',
        'CANCELED_BY_CLIENT',
        'CANCELED_BY_BANK',
        'ARCHIVE',
    ]
}

# Интервал синхронизации с CRM (секунды). Переменная CRM_REPORTS_SYNC_INTERVAL_SEC переопределяет значение.
_SYNC_INTERVAL = int(os.environ.get('CRM_REPORTS_SYNC_INTERVAL_SEC', str(5 * 60)))

# Конфигурация планировщика
SCHEDULER_CONFIG = {
    'workers_dir': './workers',
    'interval': _SYNC_INTERVAL,
    'python_cmd': 'python',  # при запуске из main/scheduler подставляется sys.executable
    # Важно: сначала токен, потом заявки, обогащение, аналитика
    'worker_order': [
        'auth_worker.py',
        'data_worker.py',
        'enrichment_worker.py',
        'analytics_worker.py',
    ],
}

# Конфигурация веб-сервера
WEB_CONFIG = {
    # Для локальной разработки безопаснее слушать только localhost
    'host': '127.0.0.1',
    # Меняем порт, чтобы не конфликтовать с другими сервисами
    'port': 5000,
    'debug': False
}

# Справочники
MANAGER_NAMES = {
    307: 'Тимонина',
    22: 'Костикова',
    281: 'Балашова',
    104: 'Боярская',
    304: 'Ганиева',
    291: 'Борзова',
    285: 'Врона',
    234: 'Овсянкина',
    61: 'Водопьянова'
}

STATUS_LABELS = {
    'DRAFT': 'Черновик',
    'NEW': 'Новая',
    'INCOME_CREATED': 'Счет создан',
    'INCOME_PAID': 'Счет оплачен',
    'INCOME_PARTIALLY_PAID': 'Частично оплачен',
    'KKT_LINKED': 'В работе',
    'COMPLETED': 'Завершена',
    'REFUND': 'Возвращен',
    'CANCELED_BY_CLIENT': 'Отменена',
    'CANCELED_BY_BANK': 'Отменено банком',
    'ARCHIVE': 'Архив',
}

STATUS_CLASSES = {
    'DRAFT': 'status-draft',
    'NEW': 'status-new',
    'INCOME_CREATED': 'status-income-created',
    'INCOME_PAID': 'status-income-paid',
    'INCOME_PARTIALLY_PAID': 'status-income-partially-paid',
    'KKT_LINKED': 'status-kkt-linked',
    'COMPLETED': 'status-completed',
    'REFUND': 'status-refund',
    'CANCELED_BY_CLIENT': 'status-canceled',
    'CANCELED_BY_BANK': 'status-canceled-by-bank',
    'ARCHIVE': 'status-archive',
}
# Конфигурация аутентификации для защищенных разделов
AUTH_CONFIG = {
    'secret_key': os.environ.get('CRM_REPORTS_SECRET_KEY', 'change_this_in_prod_please'),
    'password': os.environ.get('CRM_REPORTS_SECURE_PASSWORD', '84924525797')
}

# Конфигурация комментариев
COMMENTS_CONFIG = {
    'enabled': True,
    'max_length': 500
}

# Переменные окружения (справка):
# CRM_ANALYTICS_URL — URL admin-api аналитики (по умолчанию тот же хост, что CRM).
# CRM_REPORTS_SYNC_INTERVAL_SEC — интервал авто-синхронизации воркеров, сек (по умолчанию 300).
# CRM_REPORTS_SYNC_SECRET — если задан, ручная синхронизация /sync и /api/sync требует ?secret= или X-Sync-Secret.
# CRM_HTTP_PROXY / CRM_HTTPS_PROXY — прокси для воркеров (планировщик подставит в subprocess).
