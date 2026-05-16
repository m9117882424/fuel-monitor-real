# Fuel Monitor Real

Рабочий каркас под реальный кейс контроля лимитов топлива по машинам.

Что уже есть:
- импорт **Turpak API** по группе `#TSM BINEK ARAC`
- импорт **Shell Excel**
- импорт **Petrol** через **live API** `GET_SALES_WITH_INVOICE_INFOS`
- fallback-импорт **Petrol** из выгрузки `xlsx/csv/json`
- единое хранилище `fuel_events`
- месячная сводка по госномеру
- лимиты по машине
- alert-логика по порогам `80/90/100`
- утренний Excel-отчёт
- FastAPI-веб-панель
- базовый scheduler на 2 прогона в день

## Архитектура

Источники:
- Turpak -> `GetSales` -> нормализация -> `fuel_events`
- Shell Excel -> нормализация -> `fuel_events`
- Petrol API -> `GET_SALES_WITH_INVOICE_INFOS` -> нормализация -> `fuel_events`
- Petrol export -> fallback -> нормализация -> `fuel_events`

Сервисы:
- `sync_all()` — прогон синхронизации
- `build_monthly_vehicle_summary()` — свод по машинам
- `dispatch_alerts()` — отправка уведомлений без дублей
- `export_report()` — выгрузка Excel

API:
- `GET /health`
- `GET /` — простая веб-панель
- `GET /summary/monthly`
- `GET /events`
- `GET /dashboard/stats`
- `POST /sync/run`
- `POST /limits/upsert`
- `GET /reports/latest`

## Быстрый старт

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 1. Подготовить БД

По умолчанию используется SQLite. Для PostgreSQL:

```env
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/fuel_monitor
```

Потом создать таблицы первым запуском:

```bash
python cli_sync.py
```

### 2. Загрузить лимиты

CSV формат:

```csv
plate,monthly_limit_liters,group_name,note
34HGY122,300,,
46AJP556,350,,
```

Загрузка:

```bash
python cli_load_limits.py --limits-csv ./limits.csv
```

### 3. Настроить источники

#### Shell

```env
SHELL_INPUT_PATH=/absolute/path/4 TSM Shell.xlsx
```

или

```env
SHELL_INPUT_DIR=./data/shell
```

#### Turpak

```env
TURPAK_COMPANY_NAME=...
TURPAK_PASSWORD=...
TURPAK_GROUP_NAME=#TSM BINEK ARAC
```

#### Petrol live API

```env
PETROL_USE_API=true
PETROL_BASE_URL=https://automaticservices.petrolofisi.com.tr/AUTOMATIC_REST_SERVICES
PETROL_USER_NAME=...
PETROL_USER_PASSWORD=...
PETROL_FLEET_ID=...
# PETROL_USER_ID и PETROL_CLIENT_ROLE_ID опциональны
# PETROL_HOLDING_ID строку лучше удалить полностью, если не используешь
```

Если live API временно не используется, можно откатиться на файл:

```env
PETROL_USE_API=false
PETROL_INPUT_PATH=/absolute/path/petrol_export.xlsx
```

или

```env
PETROL_INPUT_DIR=./data/petrol
```

### 4. Выполнить синхронизацию вручную

```bash
python cli_sync.py
```

### 5. Поднять веб-сервис

```bash
python run_api.py
```

Открыть:
- `http://localhost:8000/`
- `http://localhost:8000/summary/monthly`

### 6. Включить scheduler

В `.env`:

```env
SCHEDULER_ENABLED=true
SCHEDULER_MORNING_HOUR=7
SCHEDULER_EVENING_HOUR=18
```

Тогда при запуске `run_api.py` стартуют 2 джобы:
- 07:00 — синк + отчёт + отправка отчёта в Telegram
- 18:00 — синк + проверка лимитов

## Telegram

Включение:

```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Логика:
- уведомления уходят только один раз на порог в рамках месяца
- журнал хранится в `alert_log`

## Ограничения текущей версии

1. Petrol live API подключён через GET_SALES_WITH_INVOICE_INFOS. Это ближе к реальному рабочему примеру, но первый боевой прогон всё равно надо проверить на живых данных.
2. Shell и file-based Petrol сейчас берутся из **последнего файла** в директории. Если нужна пакетная догрузка архива файлов, надо добавить ingestion batch mode.
3. Turpak сейчас ходит за окном `SYNC_DAYS_BACK`. За счёт `event_key` дубли не плодятся, но это не incremental CDC, а pragmatic sync.

## Следующий рациональный этап

- сохранить один реальный ответ Petrol в `samples/` и зафиксировать точный mapping под ваш tenant
- страница редактирования лимитов через веб
- фильтры по АЗС/источнику/топливу
- отдельный morning report с листами `Summary / Yesterday / Near Limit / Over Limit`
- docker-compose с PostgreSQL и reverse proxy


## Sync window behavior

- First successful load for `petrol` and `turpak`: from the 1st day of the current month
- Subsequent loads: last `REGULAR_SYNC_DAYS_BACK` days (default `2`)
- Turpak monetary fields are always forced to zero

Optional env:

```env
REGULAR_SYNC_DAYS_BACK=2
```

One-off manual reset for existing Turpak rows:

```bash
python cli_zero_turpak_amounts.py
```
