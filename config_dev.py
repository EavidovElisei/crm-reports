#!/usr/bin/env python3
"""
Конфигурация для разработки - использует SQLite вместо PostgreSQL
"""
import os

# Конфигурация БД для разработки (SQLite)
DB_CONFIG = {
    'host': 'localhost',
    'database': 'crm_reports_dev.db',  # SQLite файл
    'user': '',
    'password': '',
    'type': 'sqlite'  # Указываем тип БД
}

# Конфигурация CRM (оставляем как есть)
CRM_CONFIG = {
    'auth_url': 'https://kassa.bifit.com/admin-api/oauth/token',
    'api_url': 'https://kassa.bifit.com/admin-api/protected/boxed_kkm/install/list/read',
    'base_url': 'https://kassa.bifit.com',
    'admin_url': 'https://kassa.bifit.com/admin/#/main/alfabank_request/list',
    'client_id': 'cashdesk-rest-client',
    'client_secret': 'cashdesk-rest-client',
    'manager_ids': [104, 61, 22, 281, 234, 285, 289, 291],
    'statuses': ['NEW', 'DRAFT', 'INCOME_CREATED', 'INCOME_PAID', 'KKT_LINKED', 'COMPLETED', 'CANCELED_BY_CLIENT']
}

# Конфигурация планировщика
SCHEDULER_CONFIG = {
    'workers_dir': './workers',
    'interval': 15 * 60,  # 15 минут
    'python_cmd': 'python'
}

# Конфигурация веб-сервера для разработки
WEB_CONFIG = {
    'host': '127.0.0.1',  # Только локальный доступ
    'port': 8080,
    'debug': True  # Включаем режим отладки
}

# Справочники
MANAGER_NAMES = {
    22: 'Костикова А.М.',
    61: 'Водопьянова Е.С.',
    104: 'Боярская Е.А.',
    281: 'Балашова Т.',
    234: 'Овсянкина',
    285: 'Врона', 
    289: 'Зуева',
    291: 'Борзова'
}

STATUS_LABELS = {
    'DRAFT': 'Черновик',
    'NEW': 'Новая',
    'INCOME_CREATED': 'Счет создан',
    'INCOME_PAID': 'Счет оплачен',
    'KKT_LINKED': 'В работе',
    'COMPLETED': 'Завершена',
    'CANCELED_BY_CLIENT': 'Отменена'
}

STATUS_CLASSES = {
    'DRAFT': 'status-draft',
    'NEW': 'status-new',
    'INCOME_CREATED': 'status-income-created',
    'INCOME_PAID': 'status-income-paid',
    'KKT_LINKED': 'status-kkt-linked',
    'COMPLETED': 'status-completed',
    'CANCELED_BY_CLIENT': 'status-canceled'
}

# Конфигурация аутентификации для разработки
AUTH_CONFIG = {
    'secret_key': 'dev_secret_key_change_in_production',
    'password': 'dev_password'
}

# Конфигурация комментариев
COMMENTS_CONFIG = {
    'enabled': True,
    'max_length': 500
}

