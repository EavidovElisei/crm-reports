# CRM Reports — локальный запуск

Основная документация проекта — `AGENTS.md`. Здесь только особенности локальной машины.

## Запуск

```bash
cd ~/cursor_projects/crm-reports
.venv/bin/python main.py        # либо: source .venv/bin/activate && python main.py
```

Приложение: http://127.0.0.1:8080

## База данных

Локальный PostgreSQL `crm_reports` (пользователь `crm_reports_user`). Миграции — в `database/`:

```bash
psql -d crm_reports -f database/schema.sql
psql -d crm_reports -f database/analytics_table.sql
psql -d crm_reports -f database/sla_stage_comments.sql
```

## Особенности

- **Прокси.** В `config.py` задан `SCHEDULER_CONFIG['proxy'] = http://192.168.8.8:3128` (боевой прокси).
  Локально из этой сети он недоступен — запускайте с выключенным прокси:

  ```bash
  CRM_PROXY= .venv/bin/python main.py
  ```

- **Интерпретатор воркеров.** Планировщик берёт интерпретатор virtualenv (`.venv/bin/python`),
  поэтому все зависимости должны стоять в `.venv`, а не в системном Python.
- **Без сети к CRM** (401/403 или прокси недоступен) воркеры ничего не обновят — отчёты работают
  на данных, которые уже лежат в таблице `requests`.
- Версия `openpyxl` — 3.1.5: `pandas` 3.x требует её для чтения `.xlsx` в отчётах.