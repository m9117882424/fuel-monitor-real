# Fuel Monitor — Multi-Source Fuel Analytics System

Backend and analytics system for consolidating fuel transactions, monitoring limits, comparing sources and preparing dashboard-ready operational metrics.

The project focuses on real business workflows where fuel data comes from multiple providers and internal systems, then needs to be normalized, validated and displayed for management control.

---

## Business Problem

Fuel operations often involve fragmented data sources:

- fuel cards and provider APIs
- manual or semi-automatic reports
- operational limits
- daily and monthly consumption tracking
- department-level monitoring

Without automation, teams manually reconcile reports and lose visibility over anomalies and over-limit usage.

## Solution

Fuel Monitor consolidates fuel data from multiple providers, stores it in a structured database, calculates KPIs and exposes dashboard-ready views for BI systems.

## Key Capabilities

- Multi-source fuel transaction consolidation
- Daily and monthly fuel analytics
- Fuel limit monitoring
- Dashboard-ready SQL views
- FastAPI backend
- Telegram notifications
- Scheduled synchronization
- VPS-ready deployment architecture

## Tech Stack

- Python 3.11+
- FastAPI
- PostgreSQL / SQLite MVP
- SQLAlchemy
- Pandas
- OpenPyXL
- Telegram Bot API
- Linux VPS deployment
- Metabase / Power BI

## High-Level Architecture

```text
Fuel Providers / Internal Reports / Manual Uploads
                      ↓
              Data Import Layer
                      ↓
          Validation & Normalization
                      ↓
              PostgreSQL Database
                      ↓
          SQL Views / KPI Aggregates
                      ↓
          FastAPI API + BI Dashboards
```

---

# Fuel Monitor Real

Рабочая система контроля лимитов и анализа топлива по транспортным средствам.

Что уже есть:
- импорт Turpak API
- автоматический импорт Shell через TTS SOAP `GetCustomerSalesTransaction`
- аварийный fallback-импорт Shell из Excel/CSV
- импорт Petrol live API
- fallback-импорт Petrol из файлов
- единое хранилище `fuel_events`
- идемпотентная загрузка и дедупликация Shell-транзакций
- месячная сводка по госномеру
- лимиты по машине
- alert-логика по порогам 80/90/100
- FastAPI веб-панель
- scheduler

## Архитектура

Источники:
- Turpak API -> нормализация -> `fuel_events`
- Shell TTS SOAP API -> нормализация -> `fuel_events`
- Shell Excel/CSV -> аварийный fallback -> `fuel_events`
- Petrol API -> нормализация -> `fuel_events`
- Petrol export -> fallback -> `fuel_events`

Сервисы:
- `sync_all()`
- `build_monthly_vehicle_summary()`
- `dispatch_alerts()`
- `export_report()`

## Shell TTS SOAP

Основной источник Shell — метод `GetCustomerSalesTransaction` старого TTS WebService.

Синхронизация:
- при отсутствии данных за текущий месяц загружает период с начала месяца;
- при регулярных запусках повторно проверяет последние `REGULAR_SYNC_DAYS_BACK` дней;
- повторные операции не добавляются в базу;
- ручные Excel-файлы можно оставить как аварийный fallback или полностью отключить.

Минимальная конфигурация `.env`:

```env
SHELL_ENABLED=true
SHELL_USE_API=true
SHELL_BASE_URL=https://tts.turkiyeshell.com/TTS/TTSWebServices.asmx
SHELL_CUSTOMER_CODE=<customer_code>
SHELL_USER_ID=<web_service_user>
SHELL_PASSWORD=<password>
SHELL_BRANCH_CODE=<branch_code>
SHELL_TIMEOUT_SECONDS=120
SHELL_FILE_FALLBACK_ENABLED=false
```

Проверка синхронизации:

```bash
source .venv/bin/activate
python cli_sync.py
```

Успешный вызов Shell отображается в результате примерно так:

```text
SourceSyncResult(source='shell_excel', rows_loaded=0, detail='api GetCustomerSalesTransaction ... received=12')
```

`received` — количество строк, полученных от Shell. `rows_loaded` — количество новых строк, фактически добавленных после дедупликации.

## API

- GET /health
- GET /
- GET /summary/monthly
- GET /events
- GET /dashboard/stats
- POST /sync/run
- POST /limits/upsert
- GET /reports/latest

## Быстрый старт

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Telegram

Поддерживается Telegram reporting и limit alerting.

Уведомления:
- отправляются без дублей
- логируются в БД
- поддерживают limit thresholds

## Security Notes

- Never commit `.env` files
- Never commit raw provider exports
- Anonymize vehicle numbers and customer data
- Keep production API credentials outside the repository
- Avoid exposing URLs or provider identifiers in screenshots

## Roadmap

- Add anonymized screenshots
- Add dashboard SQL examples
- Add Docker Compose
- Add CI pipeline
- Add provider adapter mocks
- Add demo dataset

## Author

Maksim Anisimov — Python automation, fuel analytics, FastAPI backend and operational BI systems.
