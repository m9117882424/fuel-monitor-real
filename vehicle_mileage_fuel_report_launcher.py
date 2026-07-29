#!/usr/bin/env python3
"""Запуск отчёта с безопасными параметрами Arvento и адаптивным чтением топлива."""
from __future__ import annotations

import sys

import vehicle_mileage_fuel_report as core
from vehicle_mileage_fuel_db import get_fuel as adaptive_get_fuel

# Основной отчёт вызывает core.get_fuel. Подменяем его адаптивной реализацией,
# которая читает фактические колонки представлений и таблиц текущей БД.
core.get_fuel = adaptive_get_fuel

from vehicle_mileage_fuel_report_group import main


def add_default(option: str, value: str) -> None:
    if option not in sys.argv:
        sys.argv.extend([option, value])


if __name__ == "__main__":
    # Суточный ответ по крупной группе превышает лимит Arvento в 100 000 строк.
    # Два часа обычно проходят сразу; при необходимости основной скрипт
    # автоматически делит интервал до 15 минут.
    add_default("--arvento-chunk-hours", "2")
    add_default("--arvento-min-chunk-minutes", "15")
    add_default("--arvento-retries", "1")
    raise SystemExit(main())
