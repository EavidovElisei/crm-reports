# CRM Reports

Python/Flask-сервис синхронизирует заявки CRM BIFIT в PostgreSQL и отдаёт HTML/Excel-отчёты.

## Запуск

```bash
cd ~/cursor_projects/crm-reports
.venv/bin/python main.py          # или: source .venv/bin/activate && python main.py
```

- Веб: http://127.0.0.1:8080
- Логи: в консоль (на сервере — `logs/app.log` через systemd)
- REST-разделы: `/report`, `/report/export`, `/dashboard`, `/api/analytics`,
  `/inventory`, `/inventory/export`, `/sla`, `/export/excel`, `/excel`, `/export/status`

Создание окружения с нуля:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
psql -d crm_reports -f database/schema.sql
psql -d crm_reports -f database/analytics_table.sql
psql -d crm_reports -f database/sla_stage_comments.sql
```

## Структура

```
crm-reports/
├── main.py                     точка входа: Flask + регистрация репортеров + планировщик
├── scheduler.py                запуск воркеров по расписанию
├── config.py                   конфигурация и справочники
├── config_local.py             локальные переопределения (не в Git)
├── config_original.py          исходная конфигурация с сервера (не в Git)
├── last_comment.py             комментарии/история статусов Альфа-Банка
├── reporters/                  *_reporter.py — модули с Flask app
│   ├── crm_reporter.py            /report, /report/export
│   ├── business_dashboard_reporter.py  /dashboard, /api/analytics, SLA-метрики
│   ├── excel_export_reporter.py       /export/excel, /excel, /export/status
│   ├── inventory_reporter.py          /inventory, /inventory/export
│   └── sla_detail_reporter.py         /sla, /sla/comment, /sla/bank-comments
├── workers/                    *_worker.py, порядок задан в config.py
│   ├── auth_worker.py             токен CRM
│   ├── data_worker.py             заявки из admin-api в requests
│   ├── enrichment_worker.py       догрузка счетов/регистраций/TID
│   ├── analytics_worker.py        снимки аналитики в analytics_data
│   ├── batch_enrichment_worker.py разово: пакетное обогащение комментариями
│   └── delivery_address_backfill.py разово: догрузка адресов доставки
├── lib/                        вспомогательные модули
│   ├── region_heatmap.py          тепловая карта регионов для /sla
│   └── region_map_paths.py        контуры субъектов РФ
├── data/
│   ├── reference/              справочники (регионы ФН, адреса партнёров)
│   └── structure/              выгрузка структуры requests.data (structure.sql/.txt)
├── database/                   schema.sql, analytics_table.sql, sla_stage_comments.sql
├── docs/                       регламенты, обучение, лендинг-квиз
└── scripts/                    deploy.sh, manage.sh, start.sh, backup_db.sh, debug_comments.py
```

## Архитектура

- `main.py` запускает Flask и планировщик, динамически регистрирует все `reporters/*_reporter.py`;
  папки `reporters/` и `lib/` регистрируются как пакеты, поэтому импорты внутри репортеров — `from lib...`.
- Каждый репортёр экспортирует `app: Flask`.
- Воркеры — отдельные процессы. Запускаются интерпретатором virtualenv в порядке
  `SCHEDULER_CONFIG['worker_order']` (`auth → data → enrichment → analytics`); остальные `*_worker.py`
  автоматически не запускаются.
- Данные заявок — JSONB `requests.data`; при синхронизации сохраняются `enrichment` и уже
  полученные `deviceInfo`/TID.

## Соглашения

- Сохраняйте минимальный объём изменений и текущий стиль: русские комментарии, docstrings и UI-тексты.
- Общую логику комментариев Альфа-Банка размещайте в `last_comment.py`.
- UI генерируется inline в Python (`render_template_string`), отдельных шаблонов и статических файлов нет.
- Не коммитьте `.env`, `config_local.py`, учётные данные и другие секреты.
- Автотестов и настроенной команды линтинга в репозитории нет.

## Окружение

| Переменная | Назначение |
|---|---|
| `CRM_ANALYTICS_URL` | адрес аналитики admin-api |
| `CRM_REPORTS_SYNC_INTERVAL_SEC` | интервал запуска воркеров (по умолчанию 300 с) |
| `CRM_PROXY` | прокси для воркеров; пустое значение = без прокси |
| `CRM_REPORTS_SECRET_KEY`, `CRM_REPORTS_SECURE_PASSWORD` | секреты защищённых разделов |