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

Рабочий каркас под реальный кейс контроля лимитов топлива по машинам.

Что уже есть:
- импорт Turpak API
- импорт Shell Excel
- импорт Petrol live API
- fallback-импорт Petrol из файлов
- единое хранилище fuel_events
- месячная сводка по госномеру
- лимиты по машине
- alert-логика по порогам 80/90/100
- FastAPI веб-панель
- scheduler

## Архитектура

Источники:
- Turpak -> нормализация -> fuel_events
- Shell Excel -> нормализация -> fuel_events
- Petrol API -> нормализация -> fuel_events
- Petrol export -> fallback -> fuel_events

Сервисы:
- sync_all()
- build_monthly_vehicle_summary()
- dispatch_alerts()
- export_report()

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
