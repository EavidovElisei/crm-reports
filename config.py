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
    'client_id': 'cashdesk-rest-client',
    'client_secret': 'cashdesk-rest-client',
    'manager_ids': [104, 61, 22, 281],
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
    'debug': False
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
