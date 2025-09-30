#!/usr/bin/env python3
"""
Общие настройки для всех компонентов системы
"""

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
    'client_id': 'cashdesk-rest-client',
    'client_secret': 'cashdesk-rest-client',
    'manager_ids': [104, 61, 22, 281, 234, 285, 289, 291],
    'statuses': ['NEW', 'DRAFT', 'INCOME_CREATED', 'INCOME_PAID', 'KKT_LINKED', 'COMPLETED', 'CANCELED_BY_CLIENT']
}

# Конфигурация планировщика
SCHEDULER_CONFIG = {
    'workers_dir': './workers',
    'interval': 15 * 60,  # 15 минут
    'python_cmd': 'python'  # Изменено с python3 на python
}

# Конфигурация веб-сервера
WEB_CONFIG = {
    'host': '0.0.0.0',
    'port': 8080,
    'debug': True
}

# Конфигурация таблицы комментариев (укажите реальные значения вашей БД)
COMMENTS_CONFIG = {
    'table': 'request_comments',       # например: request_comments
    'id_column': 'request_id',         # например: request_id или order_id
    'date_column': 'sent_at',          # например: sent_at или created_at
    'text_column': 'text'              # например: text или comment
}

# Справочники
MANAGER_NAMES = {
    22: 'Костикова А.М.',
    61: 'Водопьянова Е.С.',
    104: 'Боярская Е.А.',
    281: 'Балашова Т.',
    234: 'Овсянкина А.',
    285: 'Врона Э.',
    289: 'Зуева С.В.',
    291: 'Борзова С.'
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