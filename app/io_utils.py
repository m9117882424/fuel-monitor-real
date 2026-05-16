from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def read_shell_excel(path: str | Path) -> pd.DataFrame:
    return pd.read_excel(
        path,
        sheet_name='items',
        dtype={
            'Номер карты': str,
            'Номерной знак': str,
            '#Н/Д': str,
            'Код станции': str,
            'Departman Kodu': str,
            'Название группы': str,
        },
    )


def read_tabular_file(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {'.xlsx', '.xls'}:
        return pd.read_excel(path)
    if suffix == '.csv':
        try:
            return pd.read_csv(path)
        except UnicodeDecodeError:
            return pd.read_csv(path, encoding='utf-8-sig')
    if suffix == '.json':
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            try:
                return pd.DataFrame(data)
            except ValueError:
                return pd.json_normalize(data)
        return pd.DataFrame(data)
    raise ValueError(f'Unsupported file type: {suffix}')


def read_json_payload(path: str | Path) -> Any:
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def newest_matching_file(directory: str | Path, pattern: str) -> Path | None:
    directory = Path(directory)
    if not directory.exists() or not directory.is_dir():
        return None
    matches = list(directory.glob(pattern))
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)
