from __future__ import annotations

from typing import Any

import pandas as pd

from .utils import COMMON_EVENT_COLUMNS, normalize_card_no, normalize_fuel_type, normalize_plate, parse_float, sha256_key


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COMMON_EVENT_COLUMNS)


def _series(df: pd.DataFrame, key: str, default: Any = '') -> pd.Series:
    value = df.get(key)
    if isinstance(value, pd.Series):
        return value
    return pd.Series([default] * len(df), index=df.index)


def normalize_shell_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty()

    fallback_plate = (
        _series(df, '#Н/Д')
        .fillna('')
        .astype(str)
        .str.strip()
        .replace({'Нет данных': '', '0': '', '#Н/Д': ''})
    )
    main_plate = _series(df, 'Номерной знак').fillna('').astype(str).str.strip()
    resolved_plate = main_plate.where(main_plate != '', fallback_plate)

    detail = pd.DataFrame({
        'event_dt': pd.to_datetime(_series(df, 'Date'), errors='coerce'),
        'plate': resolved_plate.apply(normalize_plate),
        'fuel_type_raw': _series(df, 'Вид топлива').fillna('').astype(str).str.strip(),
        'liters': _series(df, 'Общий литр (л)', 0).apply(parse_float),
        'unit_price_try': _series(df, 'Цена (тл)', 0).apply(parse_float),
        'amount_try': _series(df, 'Стоимость (тл)', 0).apply(parse_float),
        'discount_try': 0.0,
        'station_code': _series(df, 'Код станции').fillna('').astype(str).str.strip(),
        'station_name': _series(df, 'Название станции').fillna('').astype(str).str.strip(),
        'station_city': _series(df, 'Провинция').fillna('').astype(str).str.strip(),
        'receipt_no': _series(df, 'Номер счета').fillna('').astype(str).str.strip(),
        'card_no': _series(df, 'Номер карты').apply(normalize_card_no),
        'card_type': '',
        'group_name': _series(df, 'Название группы').fillna('').astype(str).str.strip(),
        'odometer': _series(df, 'Километраж', 0).apply(parse_float),
        'sale_type': _series(df, 'Satış Tipi').fillna('').astype(str).str.strip(),
        'department_code': _series(df, 'Departman Kodu').fillna('').astype(str).str.strip(),
    })
    detail['source'] = 'shell_excel'
    detail['external_id'] = _series(df, 'External ID').fillna('').astype(str).str.strip()
    detail['fuel_type_norm'] = detail['fuel_type_raw'].apply(normalize_fuel_type)
    detail['year_month'] = detail['event_dt'].dt.strftime('%Y-%m')
    detail['event_dt_str'] = detail['event_dt'].dt.strftime('%Y-%m-%d %H:%M:%S')

    # Keep the historical hash format so API rows match earlier Excel imports.
    # RID is stored in external_id for diagnostics and future migrations.
    detail['event_key'] = detail.apply(
        lambda row: sha256_key('shell', [
            row['event_dt_str'], row['plate'], row['fuel_type_raw'], row['liters'], row['amount_try'], row['station_name'], row['card_no']
        ]),
        axis=1,
    )
    detail = detail[detail['event_dt'].notna() & (detail['plate'] != '') & (detail['liters'] > 0)].copy()
    detail.drop(columns=['event_dt_str'], inplace=True)
    return detail[COMMON_EVENT_COLUMNS].sort_values(['event_dt', 'plate'])


def normalize_petrol_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty()

    detail = pd.DataFrame({
        'event_dt': pd.to_datetime(df.get('Alim_Tarihi'), errors='coerce'),
        'plate': df.get('PlakaNo', '').apply(normalize_plate),
        'fuel_type_raw': df.get('PRODUCT_NAME', '').fillna('').astype(str).str.strip(),
        'liters': df.get('Litre', 0).apply(parse_float),
        'unit_price_try': df.get('Litre_Birim_Fiyatı', 0).apply(parse_float),
        'amount_try': df.get('KDVli_Toplam_Tutari', df.get('Fatura_Toplam_Tutari', 0)).apply(parse_float),
        'discount_try': df.get('İndirim_Tutarı', 0).apply(parse_float),
        'station_code': '',
        'station_name': df.get('STATION_NAME', '').fillna('').astype(str).str.strip(),
        'station_city': df.get('STATION_CITY', '').fillna('').astype(str).str.strip(),
        'receipt_no': df.get('FisNo', '').fillna('').astype(str).str.strip(),
        'card_no': df.get('VIU_NO', '').fillna('').astype(str).str.strip(),
        'card_type': df.get('VIU_TYPE_NAME', '').fillna('').astype(str).str.strip(),
        'group_name': '',
        'odometer': 0.0,
        'sale_type': '',
        'department_code': '',
    })
    detail['source'] = 'petrol'
    detail['external_id'] = df.get('RRPID', '').fillna('').astype(str).str.strip()
    detail['fuel_type_norm'] = detail['fuel_type_raw'].apply(normalize_fuel_type)
    detail['year_month'] = detail['event_dt'].dt.strftime('%Y-%m')
    detail['event_dt_str'] = detail['event_dt'].dt.strftime('%Y-%m-%d %H:%M:%S')
    detail['event_key'] = detail.apply(
        lambda row: f"petrol:{row['external_id']}" if row['external_id'] else sha256_key('petrol_hash', [
            row['event_dt_str'], row['plate'], row['liters'], row['amount_try'], row['receipt_no'], row['station_name']
        ]),
        axis=1,
    )
    detail = detail[detail['event_dt'].notna() & (detail['plate'] != '') & (detail['liters'] > 0)].copy()
    detail.drop(columns=['event_dt_str'], inplace=True)
    return detail[COMMON_EVENT_COLUMNS].sort_values(['event_dt', 'plate'])


def _flatten_petrol_transaction_payload(payload: Any) -> list[dict[str, Any]]:
    """
    Supports multiple known payload shapes:
    1) Old typed transaction report:
       {customers:[{fleetGroups:[{viuList:[{plate, transactions:[...]}]}]}]}
    2) Flat list under purchaseInfoList / salesList / rows / data
    3) Raw list of transaction rows
    """
    if payload in (None, '', {}):
        return []

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ('purchaseInfoList', 'salesList', 'rows', 'data', 'items', 'transactions'):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    rows: list[dict[str, Any]] = []
    for customer in payload.get('customers') or []:
        for fleet_group in (customer or {}).get('fleetGroups') or []:
            group_name = (fleet_group or {}).get('name') or ''
            fleet_id = (fleet_group or {}).get('fleetId') or ''
            for viu in (fleet_group or {}).get('viuList') or []:
                plate = (viu or {}).get('plate') or ''
                viu_id = (viu or {}).get('viuId') or ''
                card_no = (viu or {}).get('cardNo') or ''
                for txn in (viu or {}).get('transactions') or []:
                    if not isinstance(txn, dict):
                        continue
                    row = dict(txn)
                    row.setdefault('plate', plate)
                    row.setdefault('viuId', viu_id)
                    row.setdefault('cardNo', card_no)
                    row.setdefault('fleetGroupName', group_name)
                    row.setdefault('fleetId', fleet_id)
                    rows.append(row)
    return rows


def normalize_petrol_api_payload(payload: Any) -> pd.DataFrame:
    rows = _flatten_petrol_transaction_payload(payload)
    if not rows:
        return _empty()

    df = pd.DataFrame(rows)

    station_name_series = _series(df, 'dealerName')
    if station_name_series.eq('').all():
        station_name_series = _series(df, 'stationName')
    station_city_series = _series(df, 'cityName')
    product_name_series = _series(df, 'productName')
    if product_name_series.eq('').all():
        product_name_series = _series(df, 'productType')

    detail = pd.DataFrame({
        'event_dt': pd.to_datetime(_series(df, 'transactionDate').where(_series(df, 'transactionDate') != '', _series(df, 'date')), errors='coerce'),
        'plate': _series(df, 'plate').apply(normalize_plate),
        'fuel_type_raw': pd.Series(product_name_series).fillna('').astype(str).str.strip(),
        'liters': _series(df, 'quantity').where(_series(df, 'quantity') != '', _series(df, 'totalQuantity', 0)).apply(parse_float),
        'unit_price_try': _series(df, 'unitPrice', 0).apply(parse_float),
        'amount_try': _series(df, 'amount').where(_series(df, 'amount') != '', _series(df, 'totalAmount', 0)).apply(parse_float),
        'discount_try': 0.0,
        'station_code': _series(df, 'stationId').where(_series(df, 'stationId') != '', _series(df, 'dealerCode')).fillna('').astype(str).str.strip(),
        'station_name': pd.Series(station_name_series).fillna('').astype(str).str.strip(),
        'station_city': pd.Series(station_city_series).fillna('').astype(str).str.strip(),
        'receipt_no': _series(df, 'receiptNo').fillna('').astype(str).str.strip(),
        'card_no': _series(df, 'cardNo').where(_series(df, 'cardNo') != '', _series(df, 'viuId')).fillna('').astype(str).str.strip(),
        'card_type': '',
        'group_name': _series(df, 'fleetGroupName').fillna('').astype(str).str.strip(),
        'odometer': _series(df, 'totalKm', 0).apply(parse_float),
        'sale_type': _series(df, 'transactionType').where(_series(df, 'transactionType') != '', _series(df, 'operationType')).fillna('').astype(str).str.strip(),
        'department_code': _series(df, 'fleetId').fillna('').astype(str).str.strip(),
    })
    detail['source'] = 'petrol'
    external_id_series = _series(df, 'rrpid')
    if external_id_series.eq('').all():
        external_id_series = _series(df, 'stationTrnxId')
    detail['external_id'] = pd.Series(external_id_series).fillna('').astype(str).str.strip()
    detail['fuel_type_norm'] = detail['fuel_type_raw'].apply(normalize_fuel_type)
    detail['year_month'] = detail['event_dt'].dt.strftime('%Y-%m')
    detail['event_dt_str'] = detail['event_dt'].dt.strftime('%Y-%m-%d %H:%M:%S')
    detail['event_key'] = detail.apply(
        lambda row: f"petrol:{row['external_id']}" if row['external_id'] else sha256_key('petrol_api_hash', [
            row['event_dt_str'], row['plate'], row['fuel_type_raw'], row['liters'], row['amount_try'], row['station_code'], row['receipt_no']
        ]),
        axis=1,
    )
    detail = detail[detail['event_dt'].notna() & (detail['plate'] != '') & (detail['liters'] > 0)].copy()
    detail.drop(columns=['event_dt_str'], inplace=True)
    return detail[COMMON_EVENT_COLUMNS].sort_values(['event_dt', 'plate'])


def normalize_turpak_sales(sales: list[dict[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    if sales is None:
        return _empty()
    df = sales if isinstance(sales, pd.DataFrame) else pd.DataFrame(sales)
    if df.empty:
        return _empty()

    detail = pd.DataFrame({
        'event_dt': pd.to_datetime(df.get('saleBegin'), errors='coerce'),
        'plate': df.get('licensePlateNr', '').apply(normalize_plate),
        'fuel_type_raw': df.get('productName', '').fillna('').astype(str).str.strip(),
        'liters': df.get('volume', 0).apply(parse_float),
        'unit_price_try': 0.0,
        'amount_try': 0.0,
        'discount_try': 0.0,
        'station_code': df.get('stationCode', '').fillna('').astype(str).str.strip(),
        'station_name': '',
        'station_city': '',
        'receipt_no': '',
        'card_no': '',
        'card_type': '',
        'group_name': df.get('groupName', '').fillna('').astype(str).str.strip(),
        'odometer': df.get('odoMeter', 0).apply(parse_float) if 'odoMeter' in df.columns else 0.0,
        'sale_type': '',
        'department_code': '',
    })
    detail['source'] = 'turpak'
    detail['external_id'] = df.get('id', '').fillna('').astype(str).str.strip()
    detail['fuel_type_norm'] = 'diesel'
    detail['year_month'] = detail['event_dt'].dt.strftime('%Y-%m')
    detail['event_dt_str'] = detail['event_dt'].dt.strftime('%Y-%m-%d %H:%M:%S')
    detail['event_key'] = detail.apply(
        lambda row: f"turpak:{row['external_id']}" if row['external_id'] else sha256_key('turpak_hash', [
            row['event_dt_str'], row['plate'], row['liters'], row['amount_try'], row['station_code'], row['group_name']
        ]),
        axis=1,
    )
    detail = detail[detail['event_dt'].notna() & (detail['plate'] != '') & (detail['liters'] > 0)].copy()
    detail.drop(columns=['event_dt_str'], inplace=True)
    return detail[COMMON_EVENT_COLUMNS].sort_values(['event_dt', 'plate'])
