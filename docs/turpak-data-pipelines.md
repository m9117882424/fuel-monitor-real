# Turpak data pipelines

Проект использует два независимых контура данных Turpak. Их нельзя объединять в одну таблицу, потому что они решают разные задачи.

## 1. Fuel Monitor

Операционный дашборд Fuel Monitor использует таблицу `fuel_events`.

Для Turpak в неё загружается только группа:

```text
#TSM BINEK ARAC
```

Фильтр задаётся в `.env`:

```env
TURPAK_GROUP_NAME=#TSM BINEK ARAC
```

Эти данные используются для лимитов, уведомлений, месячной сводки и отчётов приложения.

Проверка обычной синхронизации:

```bash
source .venv/bin/activate
python cli_sync.py
```

## 2. Полная база Turpak для Metabase

Metabase использует отдельную таблицу:

```text
turpak_fuel_events_all
```

В ней должны находиться все группы Turpak без фильтра `groupName`.

Полная загрузка выполняется скриптом:

```bash
python scripts/import_turpak_full.py
```

Поддерживаемые режимы:

```bash
# Предыдущие сутки
python scripts/import_turpak_full.py

# Конкретная дата
python scripts/import_turpak_full.py --date 2026-07-23

# Период
python scripts/import_turpak_full.py \
  --date-from 2026-07-01 \
  --date-to 2026-07-23

# Проверка без записи в PostgreSQL
python scripts/import_turpak_full.py \
  --date-from 2026-07-01 \
  --date-to 2026-07-23 \
  --dry-run
```

Скрипт делает UPSERT по `event_key`, поэтому повторная загрузка периода не создаёт дубликаты и обновляет ранее сохранённые операции.

## 3. Представление для Metabase

Представление `fuel_three_sources_v` должно читать:

```text
Turpak  -> turpak_fuel_events_all
Shell   -> fuel_events
Petrol  -> fuel_events
```

Нельзя заменять источник Turpak в `fuel_three_sources_v` на `fuel_events`: в `fuel_events` находится только `#TSM BINEK ARAC`, и Metabase потеряет остальные группы Turpak.

## 4. Правильная архитектура

```text
fuel_events
├── Turpak: только #TSM BINEK ARAC
├── Shell
└── Petrol

Fuel Monitor
└── читает fuel_events


turpak_fuel_events_all
└── полный Turpak по всем группам

fuel_three_sources_v
├── Turpak из turpak_fuel_events_all
├── Shell из fuel_events
└── Petrol из fuel_events

Metabase
└── читает fuel_three_sources_v
```

## 5. Проверка полноты Turpak

Проверка группы Fuel Monitor в полной таблице:

```sql
SELECT
    COUNT(*) AS operations,
    ROUND(SUM(liters)::numeric, 2) AS liters
FROM turpak_fuel_events_all
WHERE group_name = '#TSM BINEK ARAC'
  AND event_dt >= TIMESTAMP '2026-07-01 00:00:00'
  AND event_dt <  TIMESTAMP '2026-08-01 00:00:00';
```

Проверка всех групп:

```sql
SELECT
    COALESCE(NULLIF(group_name, ''), 'Без группы') AS group_name,
    COUNT(*) AS operations,
    ROUND(SUM(liters)::numeric, 2) AS liters
FROM turpak_fuel_events_all
WHERE event_dt >= TIMESTAMP '2026-07-01 00:00:00'
  AND event_dt <  TIMESTAMP '2026-08-01 00:00:00'
GROUP BY group_name
ORDER BY liters DESC;
```

## 6. Восстановление представления

Перед изменением SQL-представлений нужно сохранять их определение.

Если `fuel_three_sources_v` был временно переключён на `fuel_events`, восстановите версию, которая использует `turpak_fuel_events_all`, от владельца представления PostgreSQL.

Пример:

```bash
sudo -u postgres psql \
  -d fuel_monitor \
  -v ON_ERROR_STOP=1 \
  -f /tmp/fuel_three_sources_v_before_turpak_fix.sql
```

Файлы резервных копий, диагностические CSV, JSON-ответы Turpak, `.env` и журналы не должны попадать в Git.
