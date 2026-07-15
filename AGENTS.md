# CRM Reports

Python/Flask-сервис синхронизирует заявки CRM BIFIT в PostgreSQL и отдаёт HTML/Excel-отчёты.

## Запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
psql -h localhost -d crm_reports -U crm_reports_user -f database/schema.sql
psql -h localhost -d crm_reports -U crm_reports_user -f database/analytics_table.sql
python main.py
```

Приложение по умолчанию доступно на `http://127.0.0.1:5000`. Автотестов и настроенной команды линтинга в репозитории нет.

## Архитектура

- `main.py` запускает Flask и планировщик, а также динамически регистрирует все `reporters/*_reporter.py`.
- Каждый репортёр должен экспортировать `app: Flask`; добавляйте новый отчёт отдельным файлом `*_reporter.py`.
- Воркеры находятся в `workers/*_worker.py` и запускаются планировщиком как отдельные процессы.
- Зависимый порядок воркеров задан в `config.py`: `auth` → `data` → `enrichment` → `analytics`.
- Общая конфигурация и справочники находятся в `config.py`. Локальные переопределения держите в `config_local.py`, не добавляя их в Git.

## Соглашения

- Сохраняйте минимальный объём изменений и текущий стиль: русские комментарии, docstrings и UI-тексты.
- Данные заявок хранятся в JSONB-поле `requests.data`; при синхронизации не затирайте вложенное `enrichment`.
- Общую логику комментариев Альфа-Банка размещайте в `last_comment.py`.
- UI генерируется inline в Python (`render_template_string`), отдельных шаблонов и статических файлов нет.
- Не коммитьте `.env`, `config_local.py`, учётные данные и другие секреты. Используйте переменные окружения для продакшн-переопределений.
